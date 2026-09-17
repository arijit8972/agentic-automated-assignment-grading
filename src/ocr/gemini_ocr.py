"""Gemini one-call-per-page OCR + diagram localization.

The local Surya path OCRs each detected block in a separate VLM generation
(dozens of calls per page on CPU). This module instead sends each *whole page*
to a Gemini multimodal model in a single request and gets back an ordered list
of blocks — transcribed text for text regions and bounding boxes for diagrams —
replacing the Surya layout + per-block recognition stages entirely.

It programs against the vision-client abstraction (``src.client``); the concrete
provider is resolved by the factory, never imported here directly.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from src.client import get_vision_client
from src.logging.logger import get_logger
from src.models.submission import BBox, BlockType

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a precise OCR and layout engine for scanned exam and homework "
    "pages. You transcribe text exactly (preserving mathematics, symbols, "
    "superscripts and table contents) and locate figures/diagrams. You never "
    "invent, summarize, correct, or answer the questions — you only transcribe "
    "what is on the page."
)

_USER_PROMPT = (
    "Analyze this page and return every content region in natural reading "
    "order (top-to-bottom, left-to-right).\n"
    "Return STRICT JSON of the form:\n"
    '{"blocks": [{"type": "text"|"diagram", '
    '"box_2d": [ymin, xmin, ymax, xmax], "text": "..."}]}\n'
    "Rules:\n"
    "- box_2d coordinates are integers normalized to 0-1000 "
    "(y first, then x).\n"
    "- For a text region, set \"type\":\"text\" and \"text\" to the exact "
    "transcription of that region (keep question numbers like 'Q3' or '3.').\n"
    "- For a figure, drawing, chart or diagram, set \"type\":\"diagram\" and "
    "\"text\" to only the labels/text drawn INSIDE the figure (empty string if "
    "none). Do not describe the figure.\n"
    "- The diagram box_2d MUST fully enclose EVERY part of the figure: all "
    "arrows, force vectors, axis lines, dashed lines, tick marks and their "
    "labels — including any arrowheads that extend outward. When unsure, make "
    "the box larger rather than smaller and leave a small margin on all sides. "
    "Never clip an arrow or label.\n"
    "- Merge a printed question and its handwritten/typed answer into separate "
    "blocks when they are visually distinct.\n"
    "- Output only the JSON object, nothing else."
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass
class GeminiBlock:
    """One page region returned by Gemini, mapped to pixel coordinates."""

    order: int
    block_type: BlockType
    bbox: BBox
    text: str = ""
    labels: list[str] = field(default_factory=list)


class GeminiPageOCR:
    """OCRs whole pages via a vision model — one request per page.

    ``provider`` selects the vision client (``"gemini"`` or ``"azure"``); the
    same page-level prompt works for both.
    """

    def __init__(self, provider: str = "gemini", client: Any = None) -> None:
        # Resolved lazily so importing this module never requires the SDK/key.
        self._provider = provider
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = get_vision_client(self._provider)
        return self._client

    def recognize(self, pages: list[Image.Image]) -> list[list[GeminiBlock]]:
        """Return per-page ordered blocks (one vision call per page)."""
        client = self._ensure_client()
        results: list[list[GeminiBlock]] = []
        for page_num, page in enumerate(pages):
            png = _encode_png(page)
            response = client.complete_with_image(
                _SYSTEM_PROMPT,
                _USER_PROMPT,
                png,
                mime_type="image/png",
                response_json=True,
            )
            blocks = _parse_blocks(response.content, page.width, page.height)
            n_diagram = sum(1 for b in blocks if b.block_type == BlockType.DIAGRAM)
            logger.info(
                "Page %d: %s returned %d block(s) (%d diagram, %d text), "
                "%d token(s)",
                page_num,
                self._provider,
                len(blocks),
                n_diagram,
                len(blocks) - n_diagram,
                response.usage_tokens,
            )
            results.append(blocks)
        return results


def _encode_png(page: Image.Image) -> bytes:
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    return buffer.getvalue()


def _parse_blocks(raw: str, width: int, height: int) -> list[GeminiBlock]:
    """Parse Gemini's JSON into pixel-space blocks, tolerating minor noise."""
    text = _JSON_FENCE_RE.sub("", raw.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Gemini OCR returned unparseable JSON: %s", exc)
        return []

    raw_blocks = data.get("blocks", []) if isinstance(data, dict) else []
    blocks: list[GeminiBlock] = []
    for order, item in enumerate(raw_blocks):
        if not isinstance(item, dict):
            continue
        label = str(item.get("type", "text")).strip().lower()
        is_diagram = label in {"diagram", "figure", "picture", "chart", "image"}
        bbox = _denorm_box(item.get("box_2d"), width, height)
        content = str(item.get("text", "") or "").strip()
        if is_diagram:
            blocks.append(
                GeminiBlock(
                    order=order,
                    block_type=BlockType.DIAGRAM,
                    bbox=bbox,
                    labels=[content] if content else [],
                )
            )
        else:
            if not content:
                continue  # drop empty text regions
            blocks.append(
                GeminiBlock(
                    order=order,
                    block_type=BlockType.TEXT,
                    bbox=bbox,
                    text=content,
                )
            )
    return blocks


def _denorm_box(box: Any, width: int, height: int) -> BBox:
    """Convert Gemini's 0-1000 ``[ymin, xmin, ymax, xmax]`` to pixel bbox."""
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in box)
    except (TypeError, ValueError):
        return (0, 0, width, height)
    x0 = int(xmin / 1000.0 * width)
    y0 = int(ymin / 1000.0 * height)
    x1 = int(xmax / 1000.0 * width)
    y1 = int(ymax / 1000.0 * height)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x0 = max(0, min(x0, width))
    x1 = max(0, min(x1, width))
    y0 = max(0, min(y0, height))
    y1 = max(0, min(y1, height))
    return (x0, y0, x1, y1)
