"""Feedback generator — produces student-facing narrative from a grading report.

Takes the Step 5 report (the finalized grading record) and generates a
student-facing feedback narrative via one LLM call.
"""

from __future__ import annotations

from ..client.base import BaseLLMClient
from ..logging.logger import get_logger
from .prompts import FEEDBACK_SYSTEM_PROMPT

logger = get_logger(__name__)


def generate_feedback_from_report(
    report: dict,
    llm_client: BaseLLMClient,
) -> str:
    """Generate student-facing feedback from a grading report.

    If "student_name" is present in the report, the feedback addresses them
    by name. Otherwise it skips the greeting and starts directly.

    Args:
        report: Grading report dict (keys: source, total_questions,
            total_score, total_max_score, flagged_for_review, questions,
            and optionally student_name).
        llm_client: Provider-agnostic LLM client.

    Returns:
        Plain-text student feedback.
    """
    normalized = _normalize_report(report)
    user_message = _build_user_message(normalized)
    n_questions = normalized.get("total_questions", len(normalized.get("questions", [])))

    logger.info("Generating feedback (%d questions)", n_questions)
    response = llm_client.complete(
        system_prompt=FEEDBACK_SYSTEM_PROMPT,
        user_message=user_message,
    )
    logger.info("Feedback generated (%d tokens)", response.usage_tokens)

    return response.content


def _normalize_report(report: dict) -> dict:
    """Accept both legacy report schema and grading_report schema."""
    if "questions" in report and "total_max_score" in report:
        return report

    question_summaries = report.get("question_summaries", [])
    questions = []
    flagged: list[int] = []

    for item in question_summaries:
        q_num = item.get("question_number")
        if q_num is None:
            continue
        if item.get("flagged_for_review"):
            flagged.append(q_num)
        questions.append(
            {
                "question_number": q_num,
                "score": item.get("score", 0),
                "max_score": item.get("max_score", 0),
                "reasoning": item.get("reasoning", ""),
                "flagged_for_review": item.get("flagged_for_review", False),
            }
        )

    return {
        "source": report.get("source_path", report.get("source", "")),
        "student_name": report.get("student_name"),
        "total_questions": len(questions),
        "total_score": report.get("total_score", 0),
        "total_max_score": report.get("max_score", report.get("total_max_score", 0)),
        "flagged_for_review": flagged,
        "questions": questions,
    }


def _build_user_message(report: dict) -> str:
    """Build the user prompt from a grading report dict."""
    total_score = report.get("total_score", 0)
    total_max = report.get("total_max_score", 0)
    flagged = report.get("flagged_for_review", [])
    questions = report.get("questions", [])
    student_name = report.get("student_name")

    parts: list[str] = []

    if student_name:
        parts.append(f"Student name: {student_name}\n")

    parts.append(f"Overall score: {total_score}/{total_max}\n")

    for q in sorted(questions, key=lambda x: x.get("question_number", 0)):
        q_num = q.get("question_number", "?")
        score = q.get("score", 0)
        max_score = q.get("max_score", 0)
        reasoning = q.get("reasoning", "")

        parts.append(f"Q{q_num} ({score}/{max_score}): {reasoning}")
        if q_num in flagged:
            parts.append("  [flagged for manual review]")

    parts.append(
        "\nWrite feedback for the student based on the above results."
    )

    return "\n".join(parts)
