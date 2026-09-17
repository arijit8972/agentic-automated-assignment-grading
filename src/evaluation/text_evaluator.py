"""Text answer evaluator — grades answer_text against rubric via text LLM."""

import json
import logging
import re

from ..client.base import BaseLLMClient
from ..models.evaluation import EvaluationScore, QuestionType
from ..models.submission import ExtractedQuestion
from .prompts import TEXT_EVAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def evaluate_text(
    question: ExtractedQuestion,
    rubric_entry: str,
    client: BaseLLMClient,
    prior_context: str | None = None,
) -> EvaluationScore:
    """Grade a text-based answer against the rubric.

    Returns score + reasoning + confidence.
    """
    user_message = _build_prompt(question, rubric_entry, prior_context)
    response = client.complete(
        system_prompt=TEXT_EVAL_SYSTEM_PROMPT,
        user_message=user_message,
    )
    return _parse_response(response.content, question)


def _build_prompt(
    question: ExtractedQuestion,
    rubric_entry: str,
    prior_context: str | None = None,
) -> str:
    parts = []

    if prior_context:
        parts.append("## Prior Context (referenced answers)")
        parts.append(prior_context)
        parts.append("")

    parts.append(f"## Question {question.q_id}")
    parts.append(question.question_text)
    parts.append("")
    parts.append("## Rubric / Expected Answer")
    parts.append(rubric_entry)
    parts.append("")
    parts.append("## Student Answer")
    parts.append(question.answer_text)

    return "\n".join(parts)


def _parse_response(content: str, question: ExtractedQuestion) -> EvaluationScore:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
        else:
            logger.error(
                "Failed to parse LLM response for Q%d: %s",
                question.q_id,
                content[:200],
            )
            raise ValueError(f"Could not parse evaluation response for Q{question.q_id}")

    confidence = float(data.get("confidence", 0.5))
    return EvaluationScore(
        question_number=question.q_id,
        score=float(data["score"]),
        max_score=float(data["max_score"]),
        reasoning=data["reasoning"],
        confidence=confidence,
        question_type=QuestionType.TEXT,
        flagged_for_review=confidence < 0.7,
    )
