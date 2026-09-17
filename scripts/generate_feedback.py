"""Generate student feedback from an existing grading report.

Usage:
    python scripts/generate_feedback.py outbox/physics_question_paper_report.json

Reads a grading report (output of the evaluation pipeline) and generates
student-facing feedback using the configured LLM provider.

Output: outbox/<name>_feedback.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.client import get_llm_client
from src.config import settings
from src.feedback import generate_feedback_from_report
from src.logging.logger import get_logger

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate student feedback from a grading report"
    )
    parser.add_argument(
        "report_json", type=Path, help="Path to the grading report JSON file"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output path for feedback (default: outbox/<name>_feedback.txt)",
    )
    parser.add_argument(
        "--provider", type=str, default=None,
        help="LLM provider override (default: uses EVAL_PROVIDER env var)",
    )
    args = parser.parse_args()

    if not args.report_json.exists():
        logger.error("Report file not found: %s", args.report_json)
        sys.exit(1)

    # Load report
    report = json.loads(args.report_json.read_text(encoding="utf-8"))
    logger.info(
        "Loaded report: %s (%d questions, score %s/%s)",
        args.report_json.name,
        report.get("total_questions", 0),
        report.get("total_score", "?"),
        report.get("total_max_score", "?"),
    )

    # Init LLM client
    provider = args.provider or settings.EVAL_PROVIDER
    llm_client = get_llm_client(provider)
    llm_client.validate_config()

    # Generate feedback
    feedback_text = generate_feedback_from_report(report, llm_client)

    # Write output
    if args.output:
        out_path = args.output
    else:
        outbox = Path(settings.OUTBOX_DIR).resolve()
        outbox.mkdir(parents=True, exist_ok=True)
        stem = args.report_json.stem.replace("_report", "")
        out_path = outbox / f"{stem}_feedback.txt"

    out_path.write_text(feedback_text, encoding="utf-8")
    logger.info("Feedback written: %s", out_path)

    # Print to stdout as well
    print(f"\n{'='*60}")
    print("STUDENT FEEDBACK")
    print(f"{'='*60}\n")
    print(feedback_text)
    print()


if __name__ == "__main__":
    main()
