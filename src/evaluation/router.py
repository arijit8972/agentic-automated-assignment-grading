"""Evaluation router — routes questions by type and handles backreferences.

Per question, routes:
  text    → text_evaluator
  diagram → vision_evaluator
  mixed   → both (text + vision), takes the lower-confidence result for review

Detects backreferencing (e.g. "see Q2") and switches those questions to
sequential processing so prior context is available.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..client.base import BaseLLMClient, BaseVisionClient
from ..models.evaluation import EvaluationScore, QuestionType
from ..models.submission import ExtractedQuestion
from .text_evaluator import evaluate_text
from .vision_evaluator import evaluate_diagram

logger = logging.getLogger(__name__)

# Patterns indicating a reference to a previous answer
_BACKREF_PATTERNS = re.compile(
    "|".join([
        r"\b(?:as (?:shown|mentioned|stated|explained|described) (?:in|above))",
        r"\b(?:see|refer to) (?:question|answer|q|a|part)\s*\d+",
        r"\b(?:from|per) (?:the )?(?:previous|above|earlier)",
        r"\b(?:same as|similar to) (?:question|answer|q|a|part)\s*\d+",
        r"\b(?:using (?:the )?(?:result|answer|value) (?:from|of|in))",
    ]),
    re.IGNORECASE,
)


def evaluate_submission(
    questions: list[ExtractedQuestion],
    rubric: dict[int, str],
    llm_client: BaseLLMClient,
    vision_client: BaseVisionClient,
    max_workers: int = 4,
) -> list[EvaluationScore]:
    """Evaluate all questions from a submission.

    Uses parallel processing by default. Falls back to sequential for
    questions that backreference prior answers.

    Questions with q_id <= 0 (header/metadata blocks) are skipped.
    """
    # Filter out non-question blocks (headers, metadata with q_id <= 0)
    gradable = [q for q in questions if q.q_id > 0]
    if not gradable:
        logger.warning("No gradable questions found (all q_id <= 0)")
        return []

    questions_sorted = sorted(gradable, key=lambda q: q.q_id)

    if _has_backreferences(questions_sorted):
        logger.info("Backreferences detected — switching to sequential evaluation")
        return _evaluate_sequential(questions_sorted, rubric, llm_client, vision_client)

    logger.info("No backreferences — using parallel evaluation")
    return _evaluate_parallel(questions_sorted, rubric, llm_client, vision_client, max_workers)


def _has_backreferences(questions: list[ExtractedQuestion]) -> bool:
    """Check if any answer references a previous question."""
    for q in questions:
        if _BACKREF_PATTERNS.search(q.answer_text):
            return True
    return False


def _evaluate_parallel(
    questions: list[ExtractedQuestion],
    rubric: dict[int, str],
    llm_client: BaseLLMClient,
    vision_client: BaseVisionClient,
    max_workers: int,
) -> list[EvaluationScore]:
    """Evaluate questions concurrently using a thread pool."""
    results: list[EvaluationScore] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _route_question, q, rubric.get(q.q_id, ""), llm_client, vision_client
            ): q
            for q in questions
        }
        for future in as_completed(futures):
            q = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.error("Q%d evaluation failed: %s", q.q_id, exc)
                results.append(EvaluationScore(
                    question_number=q.q_id,
                    score=0,
                    max_score=0,
                    reasoning=f"Evaluation failed: {exc}",
                    confidence=0.0,
                    question_type=q.question_type,
                    flagged_for_review=True,
                ))

    return sorted(results, key=lambda r: r.question_number)


def _evaluate_sequential(
    questions: list[ExtractedQuestion],
    rubric: dict[int, str],
    llm_client: BaseLLMClient,
    vision_client: BaseVisionClient,
) -> list[EvaluationScore]:
    """Evaluate questions sequentially, passing prior context forward."""
    results: list[EvaluationScore] = []
    prior_parts: list[str] = []

    for q in questions:
        prior_context = "\n".join(prior_parts) if prior_parts else None
        try:
            result = _route_question(
                q, rubric.get(q.q_id, ""), llm_client, vision_client, prior_context
            )
        except Exception as exc:
            logger.error("Q%d evaluation failed: %s", q.q_id, exc)
            result = EvaluationScore(
                question_number=q.q_id,
                score=0,
                max_score=0,
                reasoning=f"Evaluation failed: {exc}",
                confidence=0.0,
                question_type=q.question_type,
                flagged_for_review=True,
            )
        results.append(result)
        prior_parts.append(
            f"Q{q.q_id}: {q.question_text}\n"
            f"A: {q.answer_text}\n"
            f"Score: {result.score}/{result.max_score}"
        )

    return results


def _route_question(
    question: ExtractedQuestion,
    rubric_entry: str,
    llm_client: BaseLLMClient,
    vision_client: BaseVisionClient,
    prior_context: str | None = None,
) -> EvaluationScore:
    """Route a single question to the appropriate evaluator by type.

    Also handles the case where the OCR declares type=text but the question
    actually has a diagram (has_diagram=True with image_crop populated).
    """
    effective_type = _effective_question_type(question)

    match effective_type:
        case QuestionType.TEXT:
            return evaluate_text(question, rubric_entry, llm_client, prior_context)
        case QuestionType.DIAGRAM:
            return evaluate_diagram(question, rubric_entry, vision_client, prior_context)
        case QuestionType.MIXED:
            return _evaluate_mixed(
                question, rubric_entry, llm_client, vision_client, prior_context
            )


def _effective_question_type(question: ExtractedQuestion) -> QuestionType:
    """Determine the effective type, correcting OCR mismatches.

    If has_diagram is True and image_crop is present but type says TEXT,
    upgrade to MIXED (has both text answer and diagram). If there's no
    answer_text at all, treat as pure DIAGRAM.
    """
    if question.has_diagram and question.image_crop is not None:
        if question.question_type == QuestionType.TEXT:
            if question.answer_text.strip():
                logger.info(
                    "Q%d: type=text but has diagram — upgrading to mixed",
                    question.q_id,
                )
                return QuestionType.MIXED
            logger.info(
                "Q%d: type=text but has diagram and no text answer — treating as diagram",
                question.q_id,
            )
            return QuestionType.DIAGRAM
    return question.question_type


def _evaluate_mixed(
    question: ExtractedQuestion,
    rubric_entry: str,
    llm_client: BaseLLMClient,
    vision_client: BaseVisionClient,
    prior_context: str | None = None,
) -> EvaluationScore:
    """Evaluate mixed questions with both text and vision, flag low confidence."""
    text_result = evaluate_text(question, rubric_entry, llm_client, prior_context)
    vision_result = evaluate_diagram(question, rubric_entry, vision_client, prior_context)

    # Use the result with higher confidence; flag if they disagree significantly
    if abs(text_result.score - vision_result.score) > (text_result.max_score * 0.3):
        text_result.flagged_for_review = True
        vision_result.flagged_for_review = True

    if vision_result.confidence >= text_result.confidence:
        return vision_result
    return text_result
