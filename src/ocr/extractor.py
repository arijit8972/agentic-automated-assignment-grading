"""Stage 3 — page images to extracted content.

Sends each page to a cloud vision model (Gemini or Azure) in a single call that
handles layout detection, text transcription, and diagram localization together.
Then groups the returned blocks into question/answer units.

Output is an ``ExtractedSubmission`` ready for the evaluation module.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from src.config import settings
from src.logging.logger import get_logger
from src.models.submission import (
    BBox,
    BlockType,
    ExtractedQuestion,
    ExtractedSubmission,
)

logger = get_logger(__name__)

# Keyword vocabulary shared by every marker matcher below. Covers the labels
# common across homework/assignment papers; extend here (not per-regex) as new
# styles appear.
_MARKER_KEYWORD = r"(?:Q(?:uestion|ues|n)?|Ex(?:ercise)?|Problem|Prob|Part)"

_QUESTION_RE = re.compile(settings.QUESTION_MARKER_PATTERN, re.IGNORECASE)
# Locates keyword-prefixed markers (Q5, Question 5, Problem 5, ...) *inside* a
# merged block so an answer fused with the next question can be split apart.
# Only keyword forms are split on — bare numbers ("2)") occur too often inside
# answers (enumerated lists) to split on safely mid-block. The trailing
# space/capital lookahead confirms a question body follows.
_EMBEDDED_MARKER_RE = re.compile(
    r"(?<!\w)" + _MARKER_KEYWORD + r"\s*[.\-:]?\s*\d{1,3}[.\)\:\-\]/,]?(?=\s|[A-Z])",
    re.IGNORECASE,
)
# Fuzzy marker: a keyword-prefixed marker whose NUMBER was garbled by OCR into
# digit-lookalike letters (8->s, 5->o, 1->l/i, 0->O, 2->z, 6->b/g, 7->t). Used
# only to *recover* a question start the primary pattern missed; the id comes
# from sequential order, not the unreadable glyphs. Paper-agnostic: relies on
# the keyword + sequential numbering, not any one template.
_QUESTION_FUZZY_RE = re.compile(
    r"^\s*" + _MARKER_KEYWORD + r"\s*[.\-:]?\s*[\dOoSsIilZzBbGgTt]{1,3}"
    r"\s*(?:[.\)\:\-\]/,]|[A-Z])",
    re.IGNORECASE,
)
# Answer lead-in inside a block, so a question and its answer merged into one OCR
# block (common with page-level engines like Gemini) can be split into the
# question prompt and the student's answer. Matches "Ans.", "Answer:", "Sol.",
# "Solution -" etc. A separator is REQUIRED after the keyword so ordinary
# question phrasing like "Answer the following:" does not false-trigger. The
# lead-in itself is consumed (not kept in the answer).
_ANSWER_MARKER_RE = re.compile(
    r"(?<!\w)(?:Ans(?:wer)?|Sol(?:ution)?)\b\s*[.\:\-\)]\s*",
    re.IGNORECASE,
)
# A standalone max-marks tag like "[3]" or "(5)" — question metadata, not the
# student's answer, so it is dropped from answer text.
_MARK_ONLY_RE = re.compile(r"^[\[\(]\s*\d{1,3}\s*[\]\)]$")
# A trailing max-marks tag to strip off the end of an answer segment.
_TRAILING_MARK_RE = re.compile(r"\s*[\[\(]\s*\d{1,3}\s*[\]\)]\s*$")
_WS_RE = re.compile(r"\s+")


@dataclass
class _FlatBlock:
    """A page-flattened OCR block used during grouping."""

    page_num: int
    order: int
    block_type: BlockType
    bbox: BBox
    text: str = ""
    confidence: Optional[float] = None
    labels: list[str] = field(default_factory=list)


@dataclass
class _QuestionAgg:
    """Mutable accumulator for one question during grouping."""

    q_id: int
    page_num: int
    question_text: str = ""
    answer_parts: list[str] = field(default_factory=list)
    has_diagram: bool = False
    image_crop: Optional[bytes] = None
    labels: list[str] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)


class ContentExtractor:
    """Extracts text and diagram crops from a submission via a cloud vision model."""

    def __init__(self) -> None:
        self._engine = settings.OCR_ENGINE
        self._page_ocr: Any = None

    def extract(
        self,
        source_path: str | Path,
        pages: list[Image.Image],
    ) -> ExtractedSubmission:
        """Extract all questions from a submission.

        Args:
            source_path: Original submission path (recorded in the output).
            pages: Normalized page images (from the preprocessor).

        Returns:
            An ``ExtractedSubmission`` with one ``ExtractedQuestion`` per q_id.
        """
        flat = self._recognize(pages)

        questions = self._group(flat, pages)

        logger.info(
            "Extracted %d question(s) from %s (%d page(s))",
            len(questions),
            Path(source_path).name,
            len(pages),
        )
        return ExtractedSubmission(
            source_path=str(source_path),
            page_count=len(pages),
            questions=questions,
        )

    def _recognize(self, pages: list[Image.Image]) -> list[_FlatBlock]:
        """Send each page to the configured vision model and return flat blocks."""
        if self._page_ocr is None:
            from src.ocr.gemini_ocr import GeminiPageOCR

            self._page_ocr = GeminiPageOCR(provider=self._engine)

        page_blocks = self._page_ocr.recognize(pages)
        flat: list[_FlatBlock] = []
        for page_num, blocks in enumerate(page_blocks):
            for block in blocks:
                is_diagram = block.block_type == BlockType.DIAGRAM
                flat.append(
                    _FlatBlock(
                        page_num=page_num,
                        order=block.order,
                        block_type=block.block_type,
                        bbox=_clamp_bbox(block.bbox),
                        text="" if is_diagram else _collapse_repetition(block.text),
                        confidence=None,
                        labels=list(block.labels),
                    )
                )
        flat.sort(key=lambda b: (b.page_num, b.order))
        return flat

    def _group(
        self, flat: list[_FlatBlock], pages: list[Image.Image]
    ) -> list[ExtractedQuestion]:
        """Group flattened blocks into questions, assigning q_id."""
        aggs: dict[int, _QuestionAgg] = {}
        order: list[int] = []  # q_id order of first appearance
        current: Optional[_QuestionAgg] = None
        auto_id = -1  # negative ids for answer text seen before any marker
        last_seq = 0  # highest positive q_id seen, for garbled-marker recovery

        def get_or_create(q_id: int, page_num: int) -> _QuestionAgg:
            if q_id not in aggs:
                aggs[q_id] = _QuestionAgg(q_id=q_id, page_num=page_num)
                order.append(q_id)
            return aggs[q_id]

        for block in flat:
            if block.block_type == BlockType.DIAGRAM:
                if current is None:
                    current = get_or_create(auto_id, block.page_num)
                    auto_id -= 1
                self._attach_diagram(current, block, pages)
                continue

            raw_text = block.text.strip()
            if not raw_text:
                continue

            # Split at any embedded question markers so that merged OCR blocks
            # (e.g. Q9 answer + Q10 question fused into one) are handled correctly.
            for text in _split_at_markers(raw_text):
                text = text.strip()
                if not text:
                    continue

                marker = _match_question(text)
                q_id = _marker_number(marker) if marker else None
                # Garbled marker (e.g. "Qs." for Q8): recover as the next
                # question in sequence rather than dropping it into an answer.
                if q_id is None and _QUESTION_FUZZY_RE.match(text):
                    q_id = last_seq + 1

                if q_id is not None:
                    current = get_or_create(q_id, block.page_num)
                    last_seq = max(last_seq, q_id)
                    # The marker line starts the question prompt; if the answer
                    # was merged into the same block (Ans. ...), split it off.
                    q_part, a_part = _split_question_answer(text)
                    if not current.question_text:
                        current.question_text = q_part
                    if a_part and not _MARK_ONLY_RE.match(a_part):
                        current.answer_parts.append(_strip_trailing_mark(a_part))
                else:
                    if current is None:
                        current = get_or_create(auto_id, block.page_num)
                        auto_id -= 1
                    if not _MARK_ONLY_RE.match(text):
                        current.answer_parts.append(_strip_trailing_mark(text))

                if block.confidence is not None:
                    current.confidences.append(block.confidence)

        return [self._finalize(aggs[q_id]) for q_id in order]

    def _attach_diagram(
        self, agg: _QuestionAgg, block: _FlatBlock, pages: list[Image.Image]
    ) -> None:
        """Crop a diagram block and attach its bytes + labels to the question."""
        agg.has_diagram = True
        crop = _crop(pages[block.page_num], block.bbox)
        crop_bytes = _encode(crop)

        if agg.image_crop is None:
            agg.image_crop = crop_bytes
        else:
            logger.warning(
                "Question %s has multiple diagrams; keeping the first crop only",
                agg.q_id,
            )

        labels = list(block.labels)
        if labels:
            agg.labels.extend(labels)
        if block.confidence is not None:
            agg.confidences.append(block.confidence)

    def _finalize(self, agg: _QuestionAgg) -> ExtractedQuestion:
        confidence = (
            sum(agg.confidences) / len(agg.confidences) if agg.confidences else None
        )
        return ExtractedQuestion(
            q_id=agg.q_id,
            page_num=agg.page_num,
            question_text=agg.question_text,
            answer_text="\n".join(agg.answer_parts).strip(),
            has_diagram=agg.has_diagram,
            image_crop=agg.image_crop,
            labels=agg.labels,
            confidence=confidence,
        )


def _match_question(text: str) -> re.Match | None:
    """Return a regex match if *text* starts with a question marker.

    Uses the single configurable ``QUESTION_MARKER_PATTERN`` (keyword form with
    an optional separator, or a bare number with a required separator).
    """
    return _QUESTION_RE.match(text)


def _marker_number(match: re.Match) -> Optional[int]:
    """Extract the question number from a marker match, engine-independent.

    Returns the first non-empty capture group as an int, so the same helper
    works whether the (overridable) pattern uses one branch or several.
    """
    for group in match.groups():
        if group:
            try:
                return int(group)
            except ValueError:
                return None
    return None


def _split_question_answer(text: str) -> tuple[str, str]:
    """Split a question block into (question, answer) at an answer lead-in.

    Page-level OCR engines sometimes return a question and its answer as a
    single block (``Q1. ... Ans. ...``). Splitting at the first ``Ans.``/``Sol.``
    marker keeps the prompt and the student's answer in their proper fields.
    Returns ``(text, "")`` when no answer marker is present.
    """
    match = _ANSWER_MARKER_RE.search(text)
    if not match or match.start() == 0:
        return text.strip(), ""
    return text[: match.start()].strip(), text[match.end() :].strip()


def _strip_trailing_mark(text: str) -> str:
    """Remove a trailing max-marks tag (e.g. ``[3]``) from an answer segment."""
    return _TRAILING_MARK_RE.sub("", text).strip()


def _split_at_markers(text: str) -> list[str]:
    """Split *text* at embedded question-start boundaries.

    Handles OCR blocks that contain more than one question (e.g. the last
    answer and the next question merged into a single block).  The marker is
    preserved at the start of each new segment so ``_match_question`` can still
    identify it.  Returns a list with at least one element.
    """
    cuts = [m.start() for m in _EMBEDDED_MARKER_RE.finditer(text) if m.start() > 0]
    if not cuts:
        return [text]
    segments: list[str] = []
    prev = 0
    for cut in cuts:
        segments.append(text[prev:cut])
        prev = cut
    segments.append(text[prev:])
    return segments


def _collapse_repetition(text: str) -> str:
    """Collapse degenerate VLM repetition (the same phrase emitted N times).

    Block-mode VLM OCR occasionally falls into a decoding loop and repeats a
    phrase two or more times. If the text is (almost) a whole-number repeat of a
    shorter unit, keep a single copy; otherwise return it unchanged.
    """
    text = _WS_RE.sub(" ", text).strip()
    n = len(text)
    if n < 8:
        return text
    for size in range(1, n // 2 + 1):
        unit = text[:size]
        reps = 1
        while text[size * reps : size * (reps + 1)] == unit:
            reps += 1
        # Require at least one full repeat covering most of the string, so
        # non-repeated text is never truncated.
        if reps >= 2 and size * reps >= n * 0.6:
            return unit.strip()
    return text


def _clamp_bbox(bbox: Any) -> BBox:
    x0, y0, x1, y1 = (int(v) for v in bbox)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _crop(page: Image.Image, bbox: BBox) -> Image.Image:
    x0, y0, x1, y1 = bbox
    # Expand the (often too-tight) box so edge features — thin arrows, stray
    # labels — are not clipped, then clamp to the page bounds.
    pad_x = max(
        settings.DIAGRAM_CROP_PADDING_MIN_PX,
        int((x1 - x0) * settings.DIAGRAM_CROP_PADDING_RATIO),
    )
    pad_y = max(
        settings.DIAGRAM_CROP_PADDING_MIN_PX,
        int((y1 - y0) * settings.DIAGRAM_CROP_PADDING_RATIO),
    )
    x0 = max(0, min(x0 - pad_x, page.width))
    x1 = max(0, min(x1 + pad_x, page.width))
    y0 = max(0, min(y0 - pad_y, page.height))
    y1 = max(0, min(y1 + pad_y, page.height))
    return page.crop((x0, y0, x1, y1))


def _encode(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format=settings.IMAGE_CROP_FORMAT)
    return buffer.getvalue()
