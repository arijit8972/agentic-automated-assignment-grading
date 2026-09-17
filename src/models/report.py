"""Shared Pydantic DTOs for grading reports."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .evaluation import QuestionType


class ReportQuestionSummary(BaseModel):
    """Deterministic per-question summary used to assemble the report."""

    question_number: int
    question_text: str
    student_answer: str
    rubric_mapping: str
    score: float
    max_score: float
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    question_type: QuestionType
    flagged_for_review: bool = False


class GradingReport(BaseModel):
    """Structured grading report plus the generated narrative."""

    source_path: str
    page_count: int
    total_score: float
    max_score: float
    percentage: float
    report_text: str
    question_summaries: list[ReportQuestionSummary] = Field(default_factory=list)
    llm_tokens: int = 0