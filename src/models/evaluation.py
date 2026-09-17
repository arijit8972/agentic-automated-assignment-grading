"""Shared Pydantic DTOs for evaluation results.

These models are the contracts passed between modules.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    TEXT = "text"
    DIAGRAM = "diagram"
    MIXED = "mixed"


class EvaluationScore(BaseModel):
    """Per-question evaluation result."""

    question_number: int
    score: float
    max_score: float
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    question_type: QuestionType
    flagged_for_review: bool = False
