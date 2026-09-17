"""Grading report generator.

This module assembles deterministic Q/A + evaluation summaries and delegates
the narrative report text to the Azure OpenAI LLM client.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path

from src.config import settings
from src.client import BaseLLMClient, get_llm_client
from src.logging.logger import get_logger
from src.models.evaluation import EvaluationScore
from src.models.report import GradingReport, ReportQuestionSummary
from src.models.submission import ExtractedQuestion, ExtractedSubmission

from .prompts import REPORT_SYSTEM_PROMPT

logger = get_logger(__name__)


def _strip_q_prefix(text: str, q_num: int) -> str:
    """Remove the leading question marker from OCR'd question text.

    The OCR preserves the original marker (e.g. 'Q8. ', 'Q10') so the full
    question can be recovered from the raw text. The report already prints the
    heading 'Q{n}', so we strip the duplicate prefix before rendering.
    """
    return re.sub(
        rf"^\s*Q(?:uestion|ues|n)?\s*{q_num}\s*[.\):\-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def generate_report(
    submission: ExtractedSubmission,
    evaluations: list[EvaluationScore],
    rubric: dict[int, str],
    llm_client: BaseLLMClient | None = None,
) -> GradingReport:
    """Generate a structured report and a narrative summary for a submission."""
    # print submission and evaluations for debugging
    client = llm_client or get_llm_client("azure")
    evaluation_by_qid = {item.question_number: item for item in evaluations}
    question_summaries = _build_question_summaries(submission, rubric, evaluation_by_qid)
    total_score = sum(item.score for item in question_summaries)
    max_score = sum(item.max_score for item in question_summaries)
    percentage = (total_score / max_score * 100.0) if max_score else 0.0

    user_message = _build_user_message(
        submission,
        question_summaries,
        total_score,
        max_score,
        percentage,
    )
    response = client.complete(REPORT_SYSTEM_PROMPT, user_message)
    report_text = response.content.strip() or _fallback_report(
        submission, question_summaries, total_score, max_score, percentage
    )

    logger.info(
        "Generated grading report for %s with %d question summary(s)",
        submission.source_path,
        len(question_summaries),
    )
    return GradingReport(
        source_path=submission.source_path,
        page_count=submission.page_count,
        total_score=total_score,
        max_score=max_score,
        percentage=percentage,
        report_text=report_text,
        question_summaries=question_summaries,
        llm_tokens=response.usage_tokens,
    )


def render_report_markdown(report: GradingReport) -> str:
    """Render a deterministic markdown report around the generated narrative."""
    flagged = [
        f"Q{item.question_number}"
        for item in report.question_summaries
        if item.flagged_for_review
    ]
    lines = [
        "# Grading Report",
        "",
        f"- Source: {report.source_path}",
        f"- Pages: {report.page_count}",
        f"- Total score: {report.total_score}/{report.max_score}",
        f"- Percentage: {report.percentage:.2f}%",
        f"- LLM tokens: {report.llm_tokens}",
        f"- Flagged for review: {', '.join(flagged) if flagged else 'None'}",
        "",
        "## Summary",
        "",
        report.report_text.strip(),
        "",
        "## Per-Question Breakdown",
        "",
    ]
    for item in report.question_summaries:
        lines.extend(
            [
                f"### Q{item.question_number}",
                f"- Type: {item.question_type.value}",
                f"- Score: {item.score}/{item.max_score}",
                f"- Confidence: {item.confidence:.2f}",
                f"- Review flag: {'yes' if item.flagged_for_review else 'no'}",
                f"- Question: {_strip_q_prefix(item.question_text, item.question_number)}",
                f"- Student answer: {item.student_answer}",
                f"- Rubric mapping: {item.rubric_mapping}",
                f"- Evaluation reasoning: {item.reasoning}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def write_report(
    report: GradingReport,
    source_path: str | Path | None = None,
    outbox_dir: str | Path | None = None,
    feedback: str | None = None,
) -> tuple[Path, Path, Path]:
    """Write the grading report as markdown, JSON, and PDF into the outbox."""
    source = Path(source_path or report.source_path)
    out_dir = Path(outbox_dir or settings.OUTBOX_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = source.stem or "submission"
    markdown_path = out_dir / f"{stem}_grading_report.md"
    json_path = out_dir / f"{stem}_grading_report.json"
    pdf_path = out_dir / f"{stem}_grading_report.pdf"

    markdown_path.write_text(render_report_markdown(report), encoding="utf-8")
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    # Generate PDF from the report dict
    from scripts.generate_report_pdf import build_pdf
    report_dict = json.loads(report.model_dump_json())
    build_pdf(report_dict, pdf_path, feedback=feedback)

    logger.info("Wrote grading report to %s, %s, and %s", markdown_path, json_path, pdf_path)
    return markdown_path, json_path, pdf_path


def _build_question_summaries(
    submission: ExtractedSubmission,
    rubric: dict[int, str],
    evaluation_by_qid: dict[int, EvaluationScore],
) -> list[ReportQuestionSummary]:
    summaries: list[ReportQuestionSummary] = []
    for question in sorted(submission.questions, key=lambda item: item.q_id):
        evaluation = evaluation_by_qid.get(question.q_id)
        if evaluation is None:
            raise ValueError(f"Missing evaluation for Q{question.q_id}")
        summaries.append(
            ReportQuestionSummary(
                question_number=question.q_id,
                question_text=question.question_text,
                student_answer=_build_student_answer_summary(question),
                rubric_mapping=rubric.get(question.q_id, ""),
                score=evaluation.score,
                max_score=evaluation.max_score,
                reasoning=evaluation.reasoning,
                confidence=evaluation.confidence,
                question_type=evaluation.question_type,
                flagged_for_review=evaluation.flagged_for_review,
            )
        )
    return summaries


def _build_user_message(
    submission: ExtractedSubmission,
    question_summaries: list[ReportQuestionSummary],
    total_score: float,
    max_score: float,
    percentage: float,
) -> str:
    payload = OrderedDict(
        [
            ("source_path", submission.source_path),
            ("page_count", submission.page_count),
            (
                "totals",
                {
                    "score": total_score,
                    "max_score": max_score,
                    "percentage": round(percentage, 2),
                },
            ),
            (
                "questions",
                [
                    {
                        "question_number": item.question_number,
                        "question_text": item.question_text,
                        "student_answer": item.student_answer,
                        "rubric_mapping": item.rubric_mapping,
                        "score": item.score,
                        "max_score": item.max_score,
                        "confidence": item.confidence,
                        "question_type": item.question_type.value,
                        "flagged_for_review": item.flagged_for_review,
                        "reasoning": item.reasoning,
                    }
                    for item in question_summaries
                ],
            ),
        ]
    )
    return (
        "Generate a concise grading report from the structured data below. "
        "Summarize the overall result first, then note each question's score, "
        "rubric mapping, and key evaluation reasoning.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )


def _build_student_answer_summary(question: ExtractedQuestion) -> str:
    answer = question.answer_text.strip()
    labels = [label.strip() for label in question.labels if label.strip()]

    if answer and labels:
        return f"{answer}\nDiagram labels: {', '.join(labels)}"
    if answer:
        return answer
    if labels:
        return f"Diagram labels: {', '.join(labels)}"
    if question.has_diagram or question.image_crop is not None:
        return "Diagram submitted"
    return "No answer extracted"


def _fallback_report(
    submission: ExtractedSubmission,
    question_summaries: list[ReportQuestionSummary],
    total_score: float,
    max_score: float,
    percentage: float,
) -> str:
    lines = [
        "# Grading Report",
        "",
        f"- Source: {submission.source_path}",
        f"- Pages: {submission.page_count}",
        f"- Total score: {total_score}/{max_score} ({percentage:.2f}%)",
        "",
        "## Question Summary",
    ]
    for item in question_summaries:
        lines.extend(
            [
                f"### Q{item.question_number}",
                f"- Question: {_strip_q_prefix(item.question_text, item.question_number)}",
                f"- Answer: {item.student_answer}",
                f"- Rubric mapping: {item.rubric_mapping}",
                f"- Score: {item.score}/{item.max_score}",
                f"- Confidence: {item.confidence:.2f}",
                f"- Review flag: {'yes' if item.flagged_for_review else 'no'}",
                f"- Reasoning: {item.reasoning}",
                "",
            ]
        )
    return "\n".join(lines).strip()