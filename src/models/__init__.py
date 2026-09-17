"""Shared Pydantic DTOs — inter-module contracts.

Import the concrete models from their submodules, e.g.::

    from src.models.submission import ExtractedQuestion
"""
from .submission import ExtractedQuestion
from .evaluation import EvaluationScore, QuestionType
from .report import GradingReport, ReportQuestionSummary