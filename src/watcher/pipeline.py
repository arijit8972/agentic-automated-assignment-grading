"""Agentic pipeline orchestrator — autonomous grading workflow.

The watcher agent operates as a state machine that autonomously processes
submissions through a multi-step grading workflow:

    IDLE → INTAKE → OCR → RUBRIC → EVALUATION → REPORT → FEEDBACK → DONE

Each step:
  - Logs its state transition
  - Persists intermediate results (crash-safe)
  - Decides the next action based on the current output
  - Self-recovers on transient failures (retry with backoff)
  - Flags for human review when confidence is low instead of guessing

The agent is "agentic" through autonomous, unattended, event-driven operation —
only evaluation performs LLM reasoning; everything else is deterministic.
"""

from __future__ import annotations

import json
import re
import time
from enum import Enum
from pathlib import Path

from src.client import get_llm_client, get_vision_client
from src.config import settings
from src.evaluation import evaluate_submission
from src.feedback.generator import generate_feedback_from_report
from src.logging.logger import get_logger
from src.models.evaluation import EvaluationScore
from src.models.submission import ExtractedSubmission
from src.ocr.extractor import ContentExtractor
from src.ocr.preprocessor import load_pages
from src.report.generator import generate_report, write_report
from src.rubric import load_rubric_for_submission

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each attempt


class PipelineState(str, Enum):
    """States the grading agent can be in."""
    IDLE = "idle"
    INTAKE = "intake"
    OCR = "ocr"
    RUBRIC = "rubric"
    EVALUATION = "evaluation"
    REPORT = "report"
    FEEDBACK = "feedback"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class PipelineContext:
    """Mutable context carried through the agent's workflow steps."""

    def __init__(self, submission_path: Path, outbox_dir: Path | None = None) -> None:
        self.submission_path = submission_path
        self.outbox_dir = outbox_dir or Path(settings.OUTBOX_DIR).resolve()
        self.state = PipelineState.IDLE
        self.submission: ExtractedSubmission | None = None
        self.rubric: dict[int, str] = {}
        self.scores: list[EvaluationScore] = []
        self.report_path: Path | None = None
        self.feedback_path: Path | None = None
        self.errors: list[str] = []
        self.needs_human_review = False
        self.flagged_questions: list[int] = []
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.step_durations: dict[str, float] = {}
        self.step_attempts: dict[str, int] = {}
        self.source_size_bytes: int | None = None
        self.source_mtime_ns: int | None = None

    def transition(self, new_state: PipelineState) -> None:
        logger.info(
            "[%s] %s → %s",
            self.submission_path.name,
            self.state.value,
            new_state.value,
        )
        self.state = new_state


def run_pipeline(submission_path: Path, outbox_dir: Path | None = None) -> PipelineContext:
    """Execute the agentic grading workflow for one submission.

    The agent autonomously:
    1. Ingests the file and validates it
    2. Runs OCR to extract questions
    3. Locates and loads the matching rubric
    4. Evaluates each question (routing text/diagram appropriately)
    5. Decides if human review is needed based on confidence scores
    6. Generates the grading report
    7. Generates student feedback (skipped if needs review)
    8. Writes all outputs to outbox

    Returns the pipeline context with final state and all artifacts.
    """
    ctx = PipelineContext(submission_path, outbox_dir=outbox_dir)

    steps = [
        (PipelineState.INTAKE, _step_intake),
        (PipelineState.OCR, _step_ocr),
        (PipelineState.RUBRIC, _step_rubric),
        (PipelineState.EVALUATION, _step_evaluation),
        (PipelineState.REPORT, _step_report),
        (PipelineState.FEEDBACK, _step_feedback),
    ]

    logger.info("=== Agent start: %s ===", submission_path.name)

    for target_state, step_fn in steps:
        ctx.transition(target_state)
        step_start = time.perf_counter()
        success = _run_with_retry(step_fn, ctx)
        ctx.step_durations[target_state.value] = round(time.perf_counter() - step_start, 3)
        if not success:
            ctx.transition(PipelineState.FAILED)
            ctx.finished_at = time.time()
            total_seconds = round(ctx.finished_at - ctx.started_at, 2)
            logger.info("=== Total processing time before failure: %.2fs ===", total_seconds)
            _write_status(ctx)
            logger.error(
                "=== Agent FAILED at %s: %s ===",
                target_state.value,
                submission_path.name,
            )
            return ctx

    # Decide final state based on evaluation results
    if ctx.needs_human_review:
        ctx.transition(PipelineState.NEEDS_REVIEW)
        logger.info(
            "=== Agent complete (NEEDS REVIEW, Q%s): %s ===",
            ", Q".join(str(q) for q in ctx.flagged_questions),
            submission_path.name,
        )
    else:
        ctx.transition(PipelineState.DONE)
        logger.info("=== Agent complete: %s ===", submission_path.name)

    ctx.finished_at = time.time()
    total_seconds = round(ctx.finished_at - ctx.started_at, 2)
    logger.info(
        "=== Total processing time: %.2fs | Steps: %s | File: %s ===",
        total_seconds,
        " | ".join(f"{k}={v}s" for k, v in ctx.step_durations.items()),
        submission_path.name,
    )
    _write_status(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Workflow steps
# ---------------------------------------------------------------------------


def _step_intake(ctx: PipelineContext) -> None:
    """Validate the submission file exists and is a supported format."""
    path = ctx.submission_path
    if not path.exists():
        raise FileNotFoundError(f"Submission not found: {path}")
    if path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    ctx.source_size_bytes = path.stat().st_size
    ctx.source_mtime_ns = path.stat().st_mtime_ns
    logger.info("Intake: %s (%.1f KB)", path.name, path.stat().st_size / 1024)


def _step_ocr(ctx: PipelineContext) -> None:
    """Run OCR pipeline: preprocess pages, then extract content."""
    pages = load_pages(ctx.submission_path)
    logger.info("OCR preprocessor: %d page(s)", len(pages))

    extractor = ContentExtractor()
    ctx.submission = extractor.extract(ctx.submission_path, pages)
    logger.info("OCR extractor: %d question(s)", len(ctx.submission.questions))

    if not ctx.submission.questions:
        raise ValueError("No questions extracted — cannot proceed")


def _step_rubric(ctx: PipelineContext) -> None:
    """Load and validate the rubric for this submission."""
    ctx.rubric = load_rubric_for_submission(ctx.submission_path)
    if not ctx.rubric:
        raise ValueError(f"No rubric found for {ctx.submission_path.name}")

    gradable_qids = sorted({q.q_id for q in ctx.submission.questions if q.q_id > 0})
    missing = [qid for qid in gradable_qids if qid not in ctx.rubric]
    if missing:
        raise ValueError(
            "Rubric is missing criteria for extracted questions: "
            + ", ".join(f"Q{q}" for q in missing)
        )
    logger.info("Rubric loaded: %d entries", len(ctx.rubric))


def _step_evaluation(ctx: PipelineContext) -> None:
    """Score each question against the rubric. Flag low-confidence for review."""
    provider = settings.EVAL_PROVIDER
    llm_client = get_llm_client(provider)
    llm_client.validate_config()
    vision_client = get_vision_client(provider)
    vision_client.validate_config()

    ctx.scores = evaluate_submission(
        questions=ctx.submission.questions,
        rubric=ctx.rubric,
        llm_client=llm_client,
        vision_client=vision_client,
    )
    _enforce_rubric_score_bounds(ctx.scores, ctx.rubric)
    _flag_low_ocr_confidence(ctx)
    logger.info("Evaluation: %d score(s)", len(ctx.scores))

    # Agent decision: check which questions need human review
    ctx.flagged_questions = sorted({
        s.question_number for s in ctx.scores if s.flagged_for_review
    })
    if ctx.flagged_questions:
        ctx.needs_human_review = True
        logger.warning(
            "Agent flagged Q%s for human review (low confidence or error)",
            ", Q".join(str(q) for q in ctx.flagged_questions),
        )


def _step_report(ctx: PipelineContext) -> None:
    """Generate and write the structured grading report to outbox."""
    provider = settings.EVAL_PROVIDER
    llm_client = get_llm_client(provider)
    llm_client.validate_config()

    # Filter to only gradable questions (q_id > 0) — header blocks are skipped
    gradable_submission = ExtractedSubmission(
        source_path=ctx.submission.source_path,
        page_count=ctx.submission.page_count,
        questions=[q for q in ctx.submission.questions if q.q_id > 0],
    )

    report = generate_report(
        submission=gradable_submission,
        evaluations=ctx.scores,
        rubric=ctx.rubric,
        llm_client=llm_client,
    )
    _, json_path, _ = write_report(report, source_path=ctx.submission_path, outbox_dir=ctx.outbox_dir)
    ctx.report_path = json_path
    logger.info("Report written: %s", json_path.name)


def _step_feedback(ctx: PipelineContext) -> None:
    """Generate student-facing feedback and write to outbox.

    If the submission needs human review, the agent still generates AI
    feedback as a draft for the teacher to review/edit.
    """
    provider = settings.EVAL_PROVIDER
    llm_client = get_llm_client(provider)
    llm_client.validate_config()

    report_dict = json.loads(ctx.report_path.read_text(encoding="utf-8"))
    feedback_payload = _build_feedback_payload(report_dict)
    feedback_text = generate_feedback_from_report(feedback_payload, llm_client)

    if ctx.needs_human_review:
        # Save as draft — teacher must approve before releasing to student
        ctx.feedback_path = ctx.outbox_dir / f"{ctx.submission_path.stem}_feedback_draft.txt"
        feedback_text = (
            "[DRAFT — NEEDS TEACHER REVIEW]\n"
            f"Flagged questions: Q{', Q'.join(str(q) for q in ctx.flagged_questions)}\n"
            "---\n\n"
            + feedback_text
        )
    else:
        ctx.feedback_path = ctx.outbox_dir / f"{ctx.submission_path.stem}_feedback.txt"

    ctx.feedback_path.write_text(feedback_text, encoding="utf-8")
    logger.info("Feedback written: %s", ctx.feedback_path.name)

    # Append feedback to the markdown report
    md_path = ctx.outbox_dir / f"{ctx.submission_path.stem}_grading_report.md"
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
        md_content += "\n\n## Student Feedback\n\n" + feedback_text + "\n"
        md_path.write_text(md_content, encoding="utf-8")
        logger.info("Feedback appended to report: %s", md_path.name)

    # Regenerate PDF with feedback included
    pdf_path = ctx.outbox_dir / f"{ctx.submission_path.stem}_grading_report.pdf"
    try:
        from scripts.generate_report_pdf import build_pdf
        report_dict = json.loads(ctx.report_path.read_text(encoding="utf-8"))
        build_pdf(report_dict, pdf_path, feedback=feedback_text)
        logger.info("PDF regenerated with feedback: %s", pdf_path.name)
    except Exception as exc:
        logger.warning("PDF regeneration failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Agent infrastructure
# ---------------------------------------------------------------------------


def _run_with_retry(step_fn, ctx: PipelineContext) -> bool:
    """Run a step with exponential backoff retry on transient failures."""
    delay = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        ctx.step_attempts[ctx.state.value] = attempt
        try:
            step_fn(ctx)
            return True
        except (FileNotFoundError, ValueError) as exc:
            # Deterministic failures — don't retry
            ctx.errors.append(f"{ctx.state.value}: {exc}")
            logger.error("[%s] %s (no retry)", ctx.state.value, exc)
            return False
        except Exception as exc:
            ctx.errors.append(f"{ctx.state.value} attempt {attempt}: {exc}")
            if attempt == MAX_RETRIES:
                logger.error(
                    "[%s] Failed after %d attempts: %s",
                    ctx.state.value, MAX_RETRIES, exc,
                )
                return False
            logger.warning(
                "[%s] Attempt %d failed (%s), retrying in %ds...",
                ctx.state.value, attempt, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
    return False


def _write_status(ctx: PipelineContext) -> None:
    """Persist the agent's final status to outbox for tracking."""
    ctx.outbox_dir.mkdir(parents=True, exist_ok=True)
    status_path = ctx.outbox_dir / f"{ctx.submission_path.stem}_status.json"

    status = {
        "source": str(ctx.submission_path),
        "source_size_bytes": ctx.source_size_bytes,
        "source_mtime_ns": ctx.source_mtime_ns,
        "state": ctx.state.value,
        "needs_human_review": ctx.needs_human_review,
        "flagged_questions": ctx.flagged_questions,
        "total_questions": len(ctx.scores),
        "total_score": sum(s.score for s in ctx.scores) if ctx.scores else None,
        "total_max_score": sum(s.max_score for s in ctx.scores) if ctx.scores else None,
        "started_at": ctx.started_at,
        "finished_at": ctx.finished_at,
        "processing_seconds": round((ctx.finished_at or time.time()) - ctx.started_at, 3),
        "step_durations": ctx.step_durations,
        "step_attempts": ctx.step_attempts,
        "report_path": str(ctx.report_path) if ctx.report_path else None,
        "feedback_path": str(ctx.feedback_path) if ctx.feedback_path else None,
        "errors": ctx.errors,
    }
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    logger.info("Status written: %s", status_path.name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_rubric_max_score(rubric_entry: str) -> float | None:
    match = re.search(r"Maximum score:\s*([0-9]+(?:\.[0-9]+)?)", rubric_entry)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _enforce_rubric_score_bounds(scores: list[EvaluationScore], rubric: dict[int, str]) -> None:
    """Clamp score/max_score to rubric-declared bounds when available."""
    for score in scores:
        declared_max = _extract_rubric_max_score(rubric.get(score.question_number, ""))
        if declared_max is None:
            continue
        if score.max_score != declared_max:
            score.max_score = declared_max
        if score.score < 0:
            score.score = 0.0
            score.flagged_for_review = True
        if score.score > declared_max:
            score.score = declared_max
            score.flagged_for_review = True


def _flag_low_ocr_confidence(ctx: PipelineContext) -> None:
    """Flag questions with low OCR confidence for human review."""
    confidence_by_qid = {
        q.q_id: q.confidence for q in ctx.submission.questions if q.q_id > 0
    }
    for score in ctx.scores:
        confidence = confidence_by_qid.get(score.question_number)
        if confidence is None:
            continue
        if confidence < settings.MIN_OCR_CONFIDENCE:
            score.flagged_for_review = True
            if "Low OCR confidence" not in score.reasoning:
                score.reasoning = (
                    f"{score.reasoning} [Low OCR confidence: {confidence:.2f}]"
                )


def _build_feedback_payload(grading_report: dict) -> dict:
    """Normalize a grading-report artifact to feedback-input schema."""
    question_summaries = grading_report.get("question_summaries", [])
    questions = []
    flagged = []
    for q in question_summaries:
        q_num = q.get("question_number")
        if q_num is None:
            continue
        if q.get("flagged_for_review"):
            flagged.append(q_num)
        questions.append(
            {
                "question_number": q_num,
                "score": q.get("score", 0),
                "max_score": q.get("max_score", 0),
                "reasoning": q.get("reasoning", ""),
                "flagged_for_review": q.get("flagged_for_review", False),
            }
        )

    return {
        "source": grading_report.get("source_path", ""),
        "total_questions": len(questions),
        "total_score": grading_report.get("total_score", 0),
        "total_max_score": grading_report.get("max_score", 0),
        "flagged_for_review": flagged,
        "questions": questions,
    }
