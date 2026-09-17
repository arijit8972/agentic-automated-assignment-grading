"""Manual verification harness for the OCR pipeline.

Runs the three OCR stages end to end on a single submission file and prints a
summary. Diagram crops are written to the outbox for visual inspection.

Usage:
    python scripts/verify_ocr.py path/to/submission.pdf
    python scripts/verify_ocr.py path/to/submission.pdf --out outbox/ocr_debug

The layout/extraction stages require a running Surya inference backend
(vllm or llama.cpp). See the deployment notes at the bottom of this file.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

# Allow running as `python scripts/verify_ocr.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.logging.logger import get_logger  # noqa: E402
from src.ocr.extractor import ContentExtractor  # noqa: E402
from src.ocr.preprocessor import load_pages  # noqa: E402

logger = get_logger("verify_ocr")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the OCR pipeline.")
    parser.add_argument("submission", help="Path to a PDF/PNG/JPG submission.")
    parser.add_argument(
        "--out",
        default="outbox/ocr_debug",
        help="Directory to write diagram crops for inspection.",
    )
    parser.add_argument(
        "--pages-only",
        action="store_true",
        help="Only run the preprocessor (no Surya backend required).",
    )
    parser.add_argument(
        "--dump-json",
        metavar="FILE",
        help="Write the full ExtractedSubmission as JSON to FILE after extraction "
             "(use '-' for stdout). Useful for building evaluator stubs/fixtures.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1 — preprocessing (no backend needed).
    pages = load_pages(args.submission)
    print(f"[preprocessor] {len(pages)} page(s) loaded")
    for i, page in enumerate(pages):
        page_path = out_dir / f"page_{i}.png"
        page.save(page_path)
        print(f"  page {i}: {page.size[0]}x{page.size[1]} -> {page_path}")

    if args.pages_only:
        print("[pages-only] stopping before extraction")
        return 0

    # Layout detection is done inside the vision call — no separate stage.
    print(f"[layout] handled by OCR engine ({settings.OCR_ENGINE})")

    # Stage 2 — extraction (text + diagram crops).
    extractor = ContentExtractor()
    submission = extractor.extract(args.submission, pages)
    print(f"[extractor] {len(submission.questions)} question(s) extracted")

    for q in submission.questions:
        print(f"\n--- Q{q.q_id} (page {q.page_num}, diagram={q.has_diagram}) ---")
        if q.question_text:
            print(f"  Q: {q.question_text[:120]}")
        if q.answer_text:
            print(f"  A: {q.answer_text[:200]}")
        if q.labels:
            print(f"  labels: {q.labels}")
        if q.image_crop:
            crop_path = out_dir / f"q{q.q_id}_diagram.png"
            crop_path.write_bytes(q.image_crop)
            print(f"  diagram crop -> {crop_path} ({len(q.image_crop)} bytes)")

    if args.dump_json:
        # Build the dict by hand so the diagram PNG bytes are base64-encoded
        # explicitly. Pydantic's JSON serialiser tries to UTF-8 decode raw bytes
        # and chokes on PNG data (byte 0x89), so we avoid it for image_crop.
        payload = {
            "source_path": submission.source_path,
            "page_count": submission.page_count,
            "questions": [
                {
                    "q_id": q.q_id,
                    "page_num": q.page_num,
                    "question_text": q.question_text,
                    "question_type": getattr(
                        q.question_type, "value", q.question_type
                    ),
                    "answer_text": q.answer_text,
                    "has_diagram": q.has_diagram,
                    "image_crop": (
                        base64.b64encode(q.image_crop).decode("ascii")
                        if q.image_crop
                        else None
                    ),
                    "labels": q.labels,
                    "confidence": q.confidence,
                }
                for q in submission.questions
            ],
        }
        json_str = json.dumps(payload, indent=2)
        if args.dump_json == "-":
            print(json_str)
        else:
            dump_path = Path(args.dump_json)
            dump_path.write_text(json_str, encoding="utf-8")
            print(f"[dump] ExtractedSubmission written to {dump_path}")

    print("\n[done] OCR verification complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
