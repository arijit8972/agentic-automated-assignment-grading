"""Process an OCR JSON output through the evaluation pipeline.

Usage:
    python scripts/process_ocr_output.py <ocr_output.json> [--rubric <rubric.yaml>]

Reads an ExtractedSubmission JSON (as produced by the OCR module) and runs
evaluation → report, writing results to outbox/.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.client import get_llm_client, get_vision_client
from src.client.base import BaseLLMClient
from src.config import settings
from src.evaluation import evaluate_submission
from src.feedback import generate_feedback_from_report
from src.logging.logger import get_logger
from src.models.evaluation import EvaluationScore
from src.models.submission import ExtractedSubmission
from src.report import generate_report, write_report
from src.rubric import load_rubric_for_submission, parse_rubric_file

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_submission_from_json(path: Path) -> ExtractedSubmission:
    """Load an ExtractedSubmission from a JSON file.

    ``verify_ocr.py`` dumps ``image_crop`` as a base64 ASCII string (raw PNG
    bytes are not JSON-serializable). Decode it back to bytes here so the
    vision evaluator receives real image data — otherwise Pydantic would
    UTF-8-encode the base64 text into bytes and PIL fails with
    "cannot identify image file".
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    _decode_image_crops_inplace(data)
    return ExtractedSubmission.model_validate(data)


def _decode_image_crops_inplace(data: dict) -> None:
    """Decode base64-encoded image_crop fields produced by verify_ocr."""
    questions = data.get("questions", []) if isinstance(data, dict) else []
    for q in questions:
        crop = q.get("image_crop") if isinstance(q, dict) else None
        if not isinstance(crop, str) or not crop:
            continue
        try:
            q["image_crop"] = base64.b64decode(crop, validate=True)
        except (ValueError, binascii.Error):
            # Keep raw value so validation surfaces a clear downstream error.
            pass


def write_feedback(
    submission: ExtractedSubmission,
    scores: list[EvaluationScore],
    llm_client: BaseLLMClient,
    submission_path: Path,
) -> str:
    """Generate student-facing feedback, write it to outbox, and return the text."""
    outbox = Path(settings.OUTBOX_DIR).resolve()
    feedback_path = outbox / f"{submission_path.stem}_feedback.txt"

    # Build a report dict to pass to the feedback module
    report = {
        "source": submission.source_path,
        "total_questions": len(scores),
        "total_score": sum(s.score for s in scores),
        "total_max_score": sum(s.max_score for s in scores),
        "flagged_for_review": [s.question_number for s in scores if s.flagged_for_review],
        "questions": [s.model_dump() for s in scores],
    }
    feedback_text = generate_feedback_from_report(report, llm_client)
    feedback_path.write_text(feedback_text, encoding="utf-8")
    logger.info("Feedback written: %s", feedback_path.name)
    return feedback_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Process OCR output through evaluation")
    parser.add_argument("ocr_json", type=Path, help="Path to OCR output JSON file")
    parser.add_argument(
        "--rubric", type=Path, default=None,
        help="Path to rubric file (YAML/JSON). If omitted, searches rubrics/ dir.",
    )
    args = parser.parse_args()

    if not args.ocr_json.exists():
        logger.error("OCR output file not found: %s", args.ocr_json)
        sys.exit(1)

    # Load submission
    submission = load_submission_from_json(args.ocr_json)
    logger.info(
        "Loaded submission: %s (%d pages, %d questions)",
        submission.source_path,
        submission.page_count,
        len(submission.questions),
    )

    # Filter gradable questions
    gradable = [q for q in submission.questions if q.q_id > 0]
    if not gradable:
        logger.error("No gradable questions found (all q_id <= 0)")
        sys.exit(1)
    logger.info("Gradable questions: %d", len(gradable))

    # Load rubric
    if args.rubric:
        if not args.rubric.exists():
            logger.error("Rubric file not found: %s", args.rubric)
            sys.exit(1)
        rubric = parse_rubric_file(args.rubric)
    else:
        source_path = Path(submission.source_path)
        rubric = load_rubric_for_submission(source_path)

    if not rubric:
        logger.error("No rubric found — cannot grade")
        sys.exit(1)

    missing = sorted(q.q_id for q in gradable if q.q_id not in rubric)
    if missing:
        logger.error(
            "Rubric missing entries for extracted questions: %s",
            ", ".join(f"Q{q}" for q in missing),
        )
        sys.exit(1)

    logger.info("Rubric loaded: %d entries", len(rubric))

    # Init clients — provider selected via EVAL_PROVIDER env var (default: gemini)
    provider = settings.EVAL_PROVIDER
    llm_client = get_llm_client(provider)
    llm_client.validate_config()
    vision_client = get_vision_client(provider)
    vision_client.validate_config()

    # Evaluate only
    scores = evaluate_submission(
        questions=gradable,
        rubric=rubric,
        llm_client=llm_client,
        vision_client=vision_client,
    )
    logger.info("Evaluation complete: %d scores", len(scores))

    # Generate grading report from evaluation outputs
    gradable_submission = ExtractedSubmission(
        source_path=submission.source_path,
        page_count=submission.page_count,
        questions=gradable,
    )
    report = generate_report(
        submission=gradable_submission,
        evaluations=scores,
        rubric=rubric,
        llm_client=llm_client,
    )

    # Write outputs
    source_path = Path(submission.source_path)

    # Generate feedback text first so it can be embedded in the PDF
    feedback_text = write_feedback(submission, scores, llm_client, source_path)
    report_markdown_path, report_json_path, report_pdf_path = write_report(
        report, source_path, feedback=feedback_text
    )

    # Print summary
    total = sum(s.score for s in scores)
    max_total = sum(s.max_score for s in scores)
    flagged = [s.question_number for s in scores if s.flagged_for_review]

    print(f"\n{'='*60}")
    print(f"GRADING SUMMARY: {source_path.name}")
    print(f"{'='*60}")
    print(f"Total Score: {total}/{max_total}")
    print(f"Questions: {len(scores)}")
    print(f"Report (markdown): {report_markdown_path}")
    print(f"Report (json): {report_json_path}")
    print(f"Report (pdf): {report_pdf_path}")
    if flagged:
        print(f"Flagged for review: Q{', Q'.join(str(q) for q in flagged)}")
    print(f"{'='*60}")
    for s in sorted(scores, key=lambda x: x.question_number):
        flag = " ⚠️ REVIEW" if s.flagged_for_review else ""
        print(f"  Q{s.question_number}: {s.score}/{s.max_score} ({s.question_type.value}){flag}")
        print(f"    {s.reasoning[:80]}...")
    print()


if __name__ == "__main__":
    main()
