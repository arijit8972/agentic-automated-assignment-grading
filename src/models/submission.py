"""OCR output contracts.

These Pydantic models are the boundary objects the OCR module produces and the
evaluation module consumes. They flow:

    preprocessor (page images)
        -> layout      (QuestionBlock: where + what type, no text yet)
        -> extractor   (ExtractedQuestion: text + diagram crop + labels)

``ExtractedSubmission`` is the single object handed to the evaluation pipeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from .evaluation import QuestionType

# Axis-aligned bounding box on a page image: (x0, y0, x1, y1) in pixels.
BBox = tuple[int, int, int, int]


class BlockType(str, Enum):
    """Coarse classification of a layout block, used for evaluation routing."""

    TEXT = "text"
    DIAGRAM = "diagram"
    MIXED = "mixed"


class QuestionBlock(BaseModel):
    """A single layout region detected on a page.

    Produced by ``src/ocr/layout.py``. Carries position and type only — no text
    is extracted at this stage. ``q_id`` is assigned later, during extraction,
    once OCR'd text is available to detect question boundaries.
    """

    page_num: int
    block_type: BlockType
    region_bbox: BBox
    reading_order: int
    label: str = ""  # raw Surya layout label (e.g. "Text", "Picture", "Figure")
    q_id: Optional[int] = None
    confidence: Optional[float] = None


class ExtractedQuestion(BaseModel):
    """A fully extracted question/answer unit.

    Produced by ``src/ocr/extractor.py``. For diagram questions the original
    cropped image bytes are preserved in ``image_crop`` — never discarded — so the
    vision evaluator can grade the actual drawing rather than lossy OCR text.
    """

    q_id: int
    page_num: int
    question_text: str = ""
    question_type: QuestionType = QuestionType.TEXT
    answer_text: str = ""
    has_diagram: bool = False
    image_crop: Optional[bytes] = None  # PNG bytes of the diagram region
    labels: list[str] = Field(default_factory=list)  # text found inside a diagram
    confidence: Optional[float] = None


class ExtractedSubmission(BaseModel):
    """All questions extracted from one submission file — the OCR module output."""

    source_path: str
    page_count: int
    questions: list[ExtractedQuestion] = Field(default_factory=list)
