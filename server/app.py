"""Assignment Submission Platform — Backend Server.

A FastAPI application that bridges the UI to live grading artifacts produced by
the watcher pipeline (inbox/outbox). This server does not serve dummy data.

Run:
        cd server
        pip install fastapi uvicorn python-multipart
        uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any

import json
import logging

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
INBOX_DIR = ROOT_DIR / "inbox"
OUTBOX_DIR = ROOT_DIR / "outbox"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
REPROCESS_FAILED_SUBMISSIONS = (
    os.getenv("WATCHER_REPROCESS_FAILED_SUBMISSIONS", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

app = FastAPI(title="Assignment Grading Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _format_timestamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _feedback_path_for(stem: str) -> Path | None:
    draft = OUTBOX_DIR / f"{stem}_feedback_draft.txt"
    final = OUTBOX_DIR / f"{stem}_feedback.txt"
    if draft.exists():
        return draft
    if final.exists():
        return final
    return None


def _report_files() -> list[Path]:
    if not OUTBOX_DIR.exists():
        return []
    return sorted(OUTBOX_DIR.glob("*_grading_report.json"))


def _source_stem(report: dict[str, Any], report_path: Path) -> str:
    source = report.get("source_path") or report.get("source") or report_path.stem
    return Path(str(source)).stem


def _question_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = report.get("question_summaries") or report.get("questions") or []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q_num = item.get("question_number", item.get("number"))
        if q_num is None:
            continue
        text = item.get("question_text", item.get("text", ""))
        answer = item.get("student_answer", item.get("answer", ""))
        rows.append(
            {
                "number": int(q_num),
                "text": text,
                "answer": answer,
                "score": float(item.get("score", 0)),
                "max_score": float(item.get("max_score", 0)),
                "reasoning": item.get("reasoning", ""),
                "confidence": float(item.get("confidence", 0.0)),
                "flagged": bool(item.get("flagged_for_review", False)),
                "rubric": item.get("rubric_mapping", ""),
                "question_type": item.get("question_type", "text"),
            }
        )
    return rows


def _build_submission_from_report(report_path: Path) -> dict[str, Any] | None:
    report = _safe_read_json(report_path)
    if not report:
        return None

    stem = _source_stem(report, report_path)
    questions = _question_rows(report)
    total_score = float(report.get("total_score", 0))
    total_max = float(report.get("max_score", report.get("total_max_score", 0)))
    score_percent = round(total_score / total_max * 100.0, 2) if total_max else None
    flagged_questions = [q["number"] for q in questions if q["flagged"]]
    needs_review = bool(flagged_questions)
    feedback_path = _feedback_path_for(stem)

    feedback_summary = ""
    if feedback_path is not None:
        feedback_summary = _safe_read_text(feedback_path).strip().split("\n", 1)[0]
    if not feedback_summary:
        feedback_summary = report.get("report_text", "")[:160].strip()

    student_name = stem.replace("_", " ").title()
    submitted_at = _format_timestamp(report_path)

    return {
        "id": stem,
        "student_name": student_name,
        "filename": Path(report.get("source_path", stem)).name,
        "submitted_at": submitted_at,
        "status": "needs_review" if needs_review else "graded",
        "total_score": total_score,
        "max_score": total_max,
        "score_percent": score_percent,
        "flagged_questions": flagged_questions,
        "feedback_summary": feedback_summary,
        "ai_feedback": _safe_read_text(feedback_path) if feedback_path else None,
        "questions": [
            {
                "number": q["number"],
                "text": q["text"],
                "score": q["score"],
                "max_score": q["max_score"],
                "answer": q["answer"],
                "reasoning": q["reasoning"],
                "flagged": q["flagged"],
            }
            for q in questions
        ],
        "source_path": report.get("source_path", ""),
        "report_path": str(report_path),
        "workflow_state": "needs_review" if needs_review else "done",
    }


def _real_submissions() -> list[dict[str, Any]]:
    submissions: list[dict[str, Any]] = []
    for report_path in _report_files():
        submission = _build_submission_from_report(report_path)
        if submission is not None:
            submissions.append(submission)
    return submissions


def _status_files() -> list[Path]:
    if not OUTBOX_DIR.exists():
        return []
    return sorted(OUTBOX_DIR.glob("*_status.json"))


def _status_stem(path: Path) -> str:
    return path.stem[: -len("_status")]


def _find_source_file(stem: str) -> Path | None:
    if not INBOX_DIR.exists():
        return None
    for ext in SUPPORTED_EXTENSIONS:
        candidate = INBOX_DIR / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _workflow_status_from_state(state: str) -> str:
    if state == "done":
        return "graded"
    if state in {"needs_review", "failed"}:
        return "needs_review"
    return "pending_review"


def _build_submission_from_status(status_path: Path) -> dict[str, Any]:
    status = _safe_read_json(status_path) or {}
    stem = _status_stem(status_path)
    source_path = Path(str(status.get("source", ""))) if status.get("source") else _find_source_file(stem)
    source_name = source_path.name if isinstance(source_path, Path) else f"{stem}.pdf"
    submitted_at = _format_timestamp(source_path) if isinstance(source_path, Path) and source_path.exists() else _format_timestamp(status_path)

    state = str(status.get("state", "pending"))
    status_message = str(status.get("message", "")).strip()
    status_label = _workflow_status_from_state(state)
    report_path = Path(str(status.get("report_path", ""))) if status.get("report_path") else None
    report = _safe_read_json(report_path) if report_path else None

    questions = _question_rows(report or {})
    flagged_questions = status.get("flagged_questions") or [q["number"] for q in questions if q["flagged"]]
    feedback_path = Path(str(status.get("feedback_path", ""))) if status.get("feedback_path") else _feedback_path_for(stem)
    ai_feedback = _safe_read_text(feedback_path) if feedback_path else None

    total_score = status.get("total_score")
    total_max = status.get("total_max_score")
    if report:
        total_score = report.get("total_score", total_score)
        total_max = report.get("max_score", total_max)

    score_percent = None
    if total_score is not None and total_max not in (None, 0):
        score_percent = round(float(total_score) / float(total_max) * 100.0, 2)

    feedback_summary = ""
    if ai_feedback:
        feedback_summary = ai_feedback.strip().split("\n", 1)[0]
    elif report and report.get("report_text"):
        feedback_summary = str(report["report_text"]).strip()[:160]

    return {
        "id": stem,
        "student_name": stem.replace("_", " ").title(),
        "filename": source_name,
        "submitted_at": submitted_at,
        "status": status_label,
        "workflow_state": state,
        "status_message": status_message,
        "total_score": float(total_score) if total_score is not None else None,
        "max_score": float(total_max) if total_max is not None else None,
        "score_percent": score_percent,
        "flagged_questions": flagged_questions,
        "feedback_summary": feedback_summary,
        "ai_feedback": ai_feedback,
        "questions": [
            {
                "number": q["number"],
                "text": q["text"],
                "score": q["score"],
                "max_score": q["max_score"],
                "answer": q["answer"],
                "reasoning": q["reasoning"],
                "flagged": q["flagged"],
            }
            for q in questions
        ],
        "source_path": str(source_path) if isinstance(source_path, Path) else "",
        "report_path": str(report_path) if report_path else None,
    }


def _pending_stems_without_status() -> list[str]:
    if not INBOX_DIR.exists():
        return []
    status_stems = {_status_stem(p) for p in _status_files()}
    stems = []
    for p in INBOX_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if p.stem not in status_stems:
            stems.append(p.stem)
    return sorted(stems)


def _build_pending_submission(stem: str) -> dict[str, Any]:
    source = _find_source_file(stem)
    return {
        "id": stem,
        "student_name": stem.replace("_", " ").title(),
        "filename": source.name if source else f"{stem}.pdf",
        "submitted_at": _format_timestamp(source) if source else datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending_review",
        "workflow_state": "pending",
        "total_score": None,
        "max_score": None,
        "score_percent": None,
        "flagged_questions": [],
        "feedback_summary": "Submission received. Awaiting watcher processing.",
        "ai_feedback": None,
        "questions": [],
        "source_path": str(source) if source else "",
        "report_path": None,
    }


def _submissions() -> list[dict[str, Any]]:
    submissions = [_build_submission_from_status(path) for path in _status_files()]
    pending = [_build_pending_submission(stem) for stem in _pending_stems_without_status()]
    source = submissions + pending
    return [_apply_approval(sub) for sub in source]


def _find_submission(submission_id: str) -> dict[str, Any] | None:
    for sub in _submissions():
        if sub.get("id") == submission_id:
            return sub
    return None


def _approval_path(submission_id: str) -> Path:
    return OUTBOX_DIR / f"{submission_id}_teacher_approval.json"


def _persist_approval(submission_id: str, payload: dict[str, Any]) -> None:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    _approval_path(submission_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_approval(submission_id: str) -> dict[str, Any] | None:
    return _safe_read_json(_approval_path(submission_id))


def _apply_approval(sub: dict[str, Any]) -> dict[str, Any]:
    approval = _load_approval(str(sub.get("id", "")))
    if not approval:
        return sub

    score_map_raw = approval.get("scores", {})
    score_map: dict[int, float] = {}
    if isinstance(score_map_raw, dict):
        for key, value in score_map_raw.items():
            try:
                score_map[int(key)] = float(value)
            except (TypeError, ValueError):
                continue

    updated_questions = []
    for q in sub.get("questions", []):
        number = int(q.get("number", 0))
        if number in score_map:
            q = {**q, "score": score_map[number], "flagged": False}
        updated_questions.append(q)

    total_score = sum(float(q.get("score", 0)) for q in updated_questions)
    max_score = float(sub.get("max_score") or 0)
    score_percent = round(total_score / max_score * 100.0, 2) if max_score else None

    return {
        **sub,
        "status": "graded",
        "questions": updated_questions,
        "flagged_questions": [],
        "total_score": total_score,
        "score_percent": score_percent,
        "feedback_summary": approval.get("feedback", sub.get("feedback_summary")),
        "ai_feedback": approval.get("feedback", sub.get("ai_feedback")),
        "teacher_approved_at": approval.get("approved_at"),
    }


def _status_path(submission_id: str) -> Path:
    return OUTBOX_DIR / f"{submission_id}_status.json"


def _submission_status(submission_id: str) -> dict[str, Any]:
    status = _safe_read_json(_status_path(submission_id))
    if status:
        state = str(status.get("state", ""))
        if (
            state == "failed"
            and not REPROCESS_FAILED_SUBMISSIONS
            and _status_source_unchanged(status)
        ):
            status.setdefault(
                "message",
                "Last run failed. Watcher idempotency skipped auto-retry for unchanged files. "
                "Update/re-upload the submission to retry, or enable WATCHER_REPROCESS_FAILED_SUBMISSIONS.",
            )
        return status

    source = _find_source_file(submission_id)
    if source:
        return {
            "source": str(source),
            "state": "pending",
            "message": "Awaiting watcher processing.",
        }

    return {
        "source": "",
        "state": "pending",
        "message": "Submission not found.",
    }


def _status_source_unchanged(status: dict[str, Any]) -> bool:
    source = status.get("source")
    if not source:
        return False
    source_path = Path(str(source))
    if not source_path.exists():
        return False

    source_mtime_ns = status.get("source_mtime_ns")
    source_size = status.get("source_size_bytes")
    try:
        stat = source_path.stat()
    except Exception:
        return False
    return source_mtime_ns == stat.st_mtime_ns and source_size == stat.st_size


def _summary_payload(sub: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": sub.get("id"),
        "filename": sub.get("filename"),
        "submitted_at": sub.get("submitted_at"),
        "workflow_state": sub.get("workflow_state"),
        "status_message": sub.get("status_message"),
        "status": "graded" if sub.get("status") == "graded" else "pending_review",
    }
    if sub.get("status") == "graded":
        payload.update(
            {
                "total_score": sub.get("total_score"),
                "max_score": sub.get("max_score"),
                "score_percent": sub.get("score_percent"),
                "feedback_summary": sub.get("feedback_summary"),
                "questions": [
                    {
                        "number": q["number"],
                        "text": q["text"],
                        "score": q["score"],
                        "max_score": q["max_score"],
                    }
                    for q in sub.get("questions", [])
                ],
            }
        )
    return payload


# ---------------------------------------------------------------------------
# Student Endpoints
# ---------------------------------------------------------------------------


VALID_SUBJECTS = {"physics", "biology", "chemistry", "mathematics"}
EXAM_NAME = "june_exam_2026"


@app.get("/api/subjects")
async def get_subjects():
    """Return the list of valid subjects for the dropdown."""
    return {"subjects": sorted(VALID_SUBJECTS)}


@app.post("/api/submissions")
async def upload_submission(
    file: UploadFile = File(...),
    subject: str = Form(...),
    roll_no: str = Form(...),
):
    """Student uploads a submission. Saves to inbox/{exam}/{subject}/{roll_no}/{filename}."""
    # Validate subject
    subj = subject.strip().lower()
    if subj not in VALID_SUBJECTS:
        raise HTTPException(status_code=400, detail=f"Invalid subject. Must be one of: {sorted(VALID_SUBJECTS)}")

    # Validate roll_no
    roll = roll_no.strip()
    if not roll:
        raise HTTPException(status_code=400, detail="Roll number is required.")

    # Build directory: inbox/june_exam_2026/{subject}/{roll_no}/
    dest_dir = INBOX_DIR / EXAM_NAME / subj / roll
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Save file
    dest = dest_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)

    submission_id = f"{subj}_{roll}_{Path(file.filename).stem}"

    logger.info(
        "Submission saved: %s (subject=%s, roll_no=%s, file=%s)",
        dest, subj, roll, file.filename,
    )

    return {
        "id": submission_id,
        "status": "pending_review",
        "total_score": None,
        "max_score": None,
        "score_percent": None,
        "feedback_summary": "Submission received. Grading will begin shortly.",
        "flagged_questions": [],
        "saved_to": str(dest),
        "filename": file.filename,
        "subject": subj,
        "roll_no": roll,
    }


@app.get("/api/submissions/{submission_id}/status")
async def get_submission_status(submission_id: str):
    """Get latest watcher status for a submission id."""
    return _submission_status(submission_id)


@app.get("/api/results/{subject}/{roll_no}")
async def get_results_by_path(subject: str, roll_no: str):
    """Get grading results from outbox/june_exam_2026/{subject}/{roll_no}/.

    Returns all report/feedback/status files found for this student+subject.
    """
    subj = subject.strip().lower()
    roll = roll_no.strip()
    result_dir = OUTBOX_DIR / EXAM_NAME / subj / roll

    if not result_dir.exists():
        return {"status": "pending", "results": [], "message": "No results yet."}

    results = []
    for status_file in sorted(result_dir.glob("*_status.json")):
        status = _safe_read_json(status_file)
        if not status:
            continue
        stem = status_file.stem.replace("_status", "")

        # Read the report if available
        report_file = result_dir / f"{stem}_grading_report.json"
        report = _safe_read_json(report_file)

        # Read feedback if available
        feedback_file = result_dir / f"{stem}_feedback.txt"
        feedback_draft_file = result_dir / f"{stem}_feedback_draft.txt"
        feedback = None
        if feedback_file.exists():
            feedback = feedback_file.read_text(encoding="utf-8")
        elif feedback_draft_file.exists():
            feedback = feedback_draft_file.read_text(encoding="utf-8")

        entry = {
            "id": f"{subj}_{roll}_{stem}",
            "filename": stem,
            "subject": subj,
            "roll_no": roll,
            "state": status.get("state", "unknown"),
            "needs_human_review": status.get("needs_human_review", False),
            "flagged_questions": status.get("flagged_questions", []),
            "total_score": status.get("total_score"),
            "total_max_score": status.get("total_max_score"),
            "score_percent": (
                round(status["total_score"] / status["total_max_score"] * 100)
                if status.get("total_score") is not None and status.get("total_max_score")
                else None
            ),
            "feedback": feedback,
            "questions": report.get("question_summaries", []) if report else [],
            "errors": status.get("errors", []),
        }
        results.append(entry)

    if not results:
        return {"status": "pending", "results": [], "message": "Processing in progress."}

    # Overall status: done if any result has state=done
    overall = "done" if any(r["state"] == "done" for r in results) else results[0]["state"]
    return {"status": overall, "results": results}


@app.get("/api/report/{subject}/{roll_no}/{filename}/pdf")
async def download_report_pdf(subject: str, roll_no: str, filename: str):
    """Download the grading report as PDF."""
    subj = subject.strip().lower()
    roll = roll_no.strip()
    stem = Path(filename).stem

    pdf_path = OUTBOX_DIR / EXAM_NAME / subj / roll / f"{stem}_grading_report.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF report not found. Processing may still be in progress.")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{stem}_report.pdf",
    )


@app.get("/api/report/{subject}/{roll_no}/{filename}/md")
async def download_report_md(subject: str, roll_no: str, filename: str):
    """Download the grading report as Markdown."""
    subj = subject.strip().lower()
    roll = roll_no.strip()
    stem = Path(filename).stem

    md_path = OUTBOX_DIR / EXAM_NAME / subj / roll / f"{stem}_grading_report.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")

    return FileResponse(
        path=str(md_path),
        media_type="text/markdown",
        filename=f"{stem}_report.md",
    )


@app.get("/api/submission/{subject}/{roll_no}/{filename}/download")
async def download_answer_sheet(subject: str, roll_no: str, filename: str):
    """Download the original submitted answer sheet (PDF/image) for teacher reference."""
    subj = subject.strip().lower()
    roll = roll_no.strip()

    submission_path = INBOX_DIR / EXAM_NAME / subj / roll / filename
    if not submission_path.exists():
        raise HTTPException(status_code=404, detail="Answer sheet not found.")

    media_types = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    ext = submission_path.suffix.lower()
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(submission_path),
        media_type=media_type,
        filename=filename,
    )


@app.get("/api/report/{subject}/{roll_no}/{filename}/content")
async def get_report_content(subject: str, roll_no: str, filename: str):
    """Return the grading report markdown content as JSON for UI rendering."""
    subj = subject.strip().lower()
    roll = roll_no.strip()
    stem = Path(filename).stem

    md_path = OUTBOX_DIR / EXAM_NAME / subj / roll / f"{stem}_grading_report.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")

    content = md_path.read_text(encoding="utf-8")
    return {"markdown": content, "filename": f"{stem}_grading_report.md"}


@app.get("/api/student/results")
async def get_student_results():
    """Get all processed submissions from outbox/june_exam_2026/, most recent first."""
    exam_dir = OUTBOX_DIR / EXAM_NAME
    if not exam_dir.exists():
        return {"submissions": []}

    results = []
    for status_file in exam_dir.rglob("*_status.json"):
        status = _safe_read_json(status_file)
        if not status:
            continue

        # Derive subject/roll_no from path
        try:
            relative = status_file.parent.relative_to(exam_dir)
            parts = relative.parts
            subj = parts[0] if len(parts) > 0 else "unknown"
            roll = parts[1] if len(parts) > 1 else "unknown"
        except (ValueError, IndexError):
            subj, roll = "unknown", "unknown"

        stem = status_file.stem.replace("_status", "")
        state = status.get("state", "unknown")
        is_done = state == "done"
        needs_review = status.get("needs_human_review", False)

        entry = {
            "id": f"{subj}_{roll}_{stem}",
            "filename": f"{stem}.pdf",
            "subject": subj,
            "roll_no": roll,
            "submitted_at": _file_mtime(status_file),
            "status": "graded" if is_done else "pending_review",
            "workflow_state": state,
        }

        if is_done:
            entry["total_score"] = status.get("total_score")
            entry["max_score"] = status.get("total_max_score")
            score = status.get("total_score")
            max_s = status.get("total_max_score")
            entry["score_percent"] = round(score / max_s * 100) if score is not None and max_s else None

            # Load feedback
            feedback_file = status_file.parent / f"{stem}_feedback.txt"
            if feedback_file.exists():
                entry["feedback_summary"] = feedback_file.read_text(encoding="utf-8")[:300]

            # Load question breakdown from report
            report_file = status_file.parent / f"{stem}_grading_report.json"
            report = _safe_read_json(report_file)
            if report and "question_summaries" in report:
                entry["questions"] = [
                    {
                        "number": q.get("question_number", 0),
                        "text": q.get("question_text", ""),
                        "score": q.get("score", 0),
                        "max_score": q.get("max_score", 0),
                    }
                    for q in report["question_summaries"]
                ]
        elif needs_review:
            entry["status_message"] = f"Flagged questions: Q{', Q'.join(str(q) for q in status.get('flagged_questions', []))}"

        results.append(entry)

    # Sort by submitted_at descending (most recent first)
    results.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)
    return {"submissions": results}


# ---------------------------------------------------------------------------
# Teacher Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/teacher/submissions")
async def get_teacher_submissions():
    """Get all submissions from outbox/june_exam_2026/ for the teacher dashboard."""
    exam_dir = OUTBOX_DIR / EXAM_NAME
    if not exam_dir.exists():
        return {"submissions": []}

    submissions = []
    for status_file in sorted(exam_dir.rglob("*_status.json")):
        status = _safe_read_json(status_file)
        if not status:
            continue

        # Derive subject/roll_no from path: outbox/june_exam_2026/{subject}/{roll_no}/file_status.json
        try:
            relative = status_file.parent.relative_to(exam_dir)
            parts = relative.parts  # (subject, roll_no)
            subj = parts[0] if len(parts) > 0 else "unknown"
            roll = parts[1] if len(parts) > 1 else "unknown"
        except (ValueError, IndexError):
            subj, roll = "unknown", "unknown"

        stem = status_file.stem.replace("_status", "")
        state = status.get("state", "unknown")

        submissions.append({
            "id": f"{subj}_{roll}_{stem}",
            "student_name": f"Roll {roll}",
            "subject": subj,
            "roll_no": roll,
            "filename": f"{stem}.pdf",
            "submitted_at": _file_mtime(status_file),
            "status": "needs_review" if status.get("needs_human_review") else ("graded" if state == "done" else state),
            "total_score": status.get("total_score"),
            "max_score": status.get("total_max_score"),
            "flagged_questions": status.get("flagged_questions", []),
        })

    return {"submissions": submissions}


@app.get("/api/teacher/submissions/{subject}/{roll_no}/{filename}")
async def get_submission_detail_by_path(subject: str, roll_no: str, filename: str):
    """Get full detail for teacher review screen from structured outbox path."""
    subj = subject.strip().lower()
    roll = roll_no.strip()
    stem = Path(filename).stem

    result_dir = OUTBOX_DIR / EXAM_NAME / subj / roll
    status_file = result_dir / f"{stem}_status.json"
    report_file = result_dir / f"{stem}_grading_report.json"

    status = _safe_read_json(status_file)
    report = _safe_read_json(report_file)

    if not status:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Read feedback
    feedback_file = result_dir / f"{stem}_feedback.txt"
    feedback_draft = result_dir / f"{stem}_feedback_draft.txt"
    feedback = None
    if feedback_file.exists():
        feedback = feedback_file.read_text(encoding="utf-8")
    elif feedback_draft.exists():
        feedback = feedback_draft.read_text(encoding="utf-8")

    # Build question details from report
    questions = []
    if report and "question_summaries" in report:
        for q in report["question_summaries"]:
            questions.append({
                "number": q.get("question_number", q.get("q_id", 0)),
                "text": q.get("question_text", ""),
                "answer": q.get("student_answer", ""),
                "score": q.get("score", 0),
                "max_score": q.get("max_score", 0),
                "reasoning": q.get("reasoning", ""),
                "flagged": q.get("question_number", 0) in status.get("flagged_questions", []),
            })

    return {
        "id": f"{subj}_{roll}_{stem}",
        "student_name": f"Roll {roll}",
        "subject": subj,
        "roll_no": roll,
        "filename": f"{stem}.pdf",
        "submitted_at": _file_mtime(status_file),
        "status": status.get("state", "unknown"),
        "needs_human_review": status.get("needs_human_review", False),
        "flagged_questions": status.get("flagged_questions", []),
        "total_score": status.get("total_score"),
        "max_score": status.get("total_max_score"),
        "ai_feedback": feedback,
        "questions": questions,
    }


def _file_mtime(path: Path) -> str:
    """Return file modification time as a formatted string."""
    try:
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


class ApproveRequest(BaseModel):
    scores: dict[str, float]
    feedback: str


@app.post("/api/teacher/submissions/{subject}/{roll_no}/{filename}/approve")
async def approve_submission_by_path(subject: str, roll_no: str, filename: str, req: ApproveRequest):
    """Teacher approves scores and releases to student."""
    subj = subject.strip().lower()
    roll = roll_no.strip()
    stem = Path(filename).stem

    result_dir = OUTBOX_DIR / EXAM_NAME / subj / roll
    status_file = result_dir / f"{stem}_status.json"

    if not status_file.exists():
        raise HTTPException(status_code=404, detail="Submission not found")

    # Update the status file to mark as approved
    status = _safe_read_json(status_file) or {}
    status["state"] = "done"
    status["needs_human_review"] = False
    status["teacher_approved"] = True
    status["approved_at"] = datetime.now().isoformat(timespec="seconds")
    status["teacher_scores"] = req.scores
    status["total_score"] = sum(req.scores.values())
    status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")

    # Write the final feedback (replacing draft)
    feedback_path = result_dir / f"{stem}_feedback.txt"
    feedback_path.write_text(req.feedback, encoding="utf-8")

    # Remove draft if it exists
    draft_path = result_dir / f"{stem}_feedback_draft.txt"
    if draft_path.exists():
        draft_path.unlink()

    total = sum(req.scores.values())
    return {
        "status": "approved",
        "total_score": total,
        "feedback": req.feedback,
        "message": "Scores released to student.",
    }
