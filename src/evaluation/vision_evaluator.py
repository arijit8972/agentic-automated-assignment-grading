"""Vision evaluator — grades diagram image crops against rubric via vision model.

Always receives question_text so the model knows what to look for.
"""

import json
import logging
import re

from ..client.base import BaseVisionClient
from ..models.evaluation import EvaluationScore, QuestionType
from ..models.submission import ExtractedQuestion
from .prompts import VISION_EVAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def evaluate_diagram(
    question: ExtractedQuestion,
    rubric_entry: str,
    client: BaseVisionClient,
    prior_context: str | None = None,
) -> EvaluationScore:
    """Grade a diagram answer — the actual image, not OCR'd text.

    Args:
        question: Must have image_crop populated.
        rubric_entry: Expected visual features / grading criteria.
        client: Vision model client.
        prior_context: Optional prior Q/A context for backreferences.
    """
    if question.image_crop is None:
        raise ValueError(f"Q{question.q_id} routed as diagram but has no image_crop")

    user_message = _build_prompt(question, rubric_entry, prior_context)
    response = client.complete_with_image(
        system_prompt=VISION_EVAL_SYSTEM_PROMPT,
        user_message=user_message,
        image=question.image_crop,
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
    parts.append("## Rubric / Expected Visual Features")
    parts.append(rubric_entry)

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
                "Failed to parse vision response for Q%d: %s",
                question.q_id,
                content[:200],
            )
            raise ValueError(f"Could not parse vision response for Q{question.q_id}")

    confidence = float(data.get("confidence", 0.5))
    return EvaluationScore(
        question_number=question.q_id,
        score=float(data["score"]),
        max_score=float(data["max_score"]),
        reasoning=data["reasoning"],
        confidence=confidence,
        question_type=QuestionType.DIAGRAM,
        flagged_for_review=confidence < 0.7,
    )
