"""Watcher — watch inbox/ recursively, trigger pipeline on new file, return to idle.

Watches all subdirectories (e.g. inbox/june_exam_2026/physics/42/file.pdf).
Mirrors the input directory structure into outbox/ for organized output.
Sanitizes filenames to remove spaces/special characters.
Owns the wake → process → idle loop. No grading logic here.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.config import settings
from src.logging.logger import get_logger
from .pipeline import run_pipeline

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
STABLE_POLLS = 3
STABLE_POLL_INTERVAL_SECONDS = 0.5

# Characters to strip from filenames (keep alphanumeric, dash, underscore, dot)
_UNSAFE_RE = re.compile(r"[^\w.\-]", re.ASCII)


def _sanitize_filename(name: str) -> str:
    """Replace spaces and special characters with underscores."""
    sanitized = _UNSAFE_RE.sub("_", name)
    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def _derive_outbox_dir(submission_path: Path, inbox_root: Path) -> Path:
    """Mirror the inbox subdirectory structure into outbox.

    inbox/june_exam_2026/physics/42/file.pdf
      -> outbox/june_exam_2026/physics/42/
    """
    try:
        relative = submission_path.parent.relative_to(inbox_root)
    except ValueError:
        relative = Path("")
    outbox = Path(settings.OUTBOX_DIR).resolve() / relative
    outbox.mkdir(parents=True, exist_ok=True)
    return outbox


def _status_path_for(submission_path: Path, inbox_root: Path) -> Path:
    """Get the status file path in the mirrored outbox directory."""
    outbox_dir = _derive_outbox_dir(submission_path, inbox_root)
    return outbox_dir / f"{submission_path.stem}_status.json"


def _already_processed(path: Path, inbox_root: Path) -> bool:
    """Check if a terminal status file exists for this submission."""
    status_path = _status_path_for(path, inbox_root)
    if not status_path.exists():
        return False
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    state = str(data.get("state", ""))
    terminal_states = {"done", "needs_review"}
    if not getattr(settings, "REPROCESS_FAILED_SUBMISSIONS", False):
        terminal_states.add("failed")

    if state not in terminal_states:
        return False

    # Check if the source file has changed since last processing
    source_mtime_ns = data.get("source_mtime_ns")
    source_size = data.get("source_size_bytes")
    if source_mtime_ns is None or source_size is None:
        return True  # No metadata recorded — assume processed

    try:
        stat = path.stat()
        return source_mtime_ns == stat.st_mtime_ns and source_size == stat.st_size
    except OSError:
        return False


class _SubmissionHandler(FileSystemEventHandler):
    """React to new files anywhere in the inbox directory tree."""

    def __init__(self, inbox_root: Path) -> None:
        super().__init__()
        self._inbox_root = inbox_root

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.debug("Ignoring non-submission file: %s", path.name)
            return

        # Sanitize filename if needed
        path = _ensure_sanitized(path)
        logger.info("New submission detected: %s", path)
        _process(path, self._inbox_root)


def _ensure_sanitized(path: Path) -> Path:
    """Rename file on disk if its name contains unsafe characters. Returns the final path."""
    sanitized_name = _sanitize_filename(path.name)
    if sanitized_name == path.name:
        return path
    new_path = path.parent / sanitized_name
    if new_path.exists():
        # Avoid collision — append a suffix
        stem = new_path.stem
        ext = new_path.suffix
        counter = 1
        while new_path.exists():
            new_path = path.parent / f"{stem}_{counter}{ext}"
            counter += 1
    path.rename(new_path)
    logger.info("Sanitized filename: %s -> %s", path.name, new_path.name)
    return new_path


def _process(path: Path, inbox_root: Path) -> None:
    """Run the grading pipeline for a single submission (skip if already done)."""
    if not _wait_until_stable(path):
        logger.warning("Skipping unstable or missing file: %s", path)
        return
    if _already_processed(path, inbox_root):
        logger.info("Skipping already-processed: %s", path)
        return

    try:
        outbox_dir = _derive_outbox_dir(path, inbox_root)
        run_pipeline(path, outbox_dir=outbox_dir)
    except Exception:
        logger.exception("Pipeline failed for %s", path.name)


def start(*, once: bool = False) -> None:
    """Start watching the inbox directory (recursive).

    Args:
        once: If True, process all existing files and exit without watching.
              Useful for batch/manual runs.
    """
    inbox = Path(settings.INBOX_DIR).resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    logger.info("Watching %s (recursive) for submissions...", inbox)

    if once:
        _process_existing(inbox)
        return

    observer = Observer()
    observer.schedule(_SubmissionHandler(inbox), str(inbox), recursive=True)
    observer.start()

    # Process any files already present at startup
    _process_existing(inbox)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down watcher")
    finally:
        observer.stop()
        observer.join()


def _process_existing(inbox: Path) -> None:
    """Recursively process any submission files in the inbox tree."""
    for path in sorted(inbox.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            sanitized = _ensure_sanitized(path)
            _process(sanitized, inbox)


def _wait_until_stable(path: Path) -> bool:
    """Wait until file size/mtime stop changing across a few polls."""
    if not path.exists():
        return False

    previous: tuple[int, int] | None = None
    stable_count = 0
    for _ in range(STABLE_POLLS * 3):
        if not path.exists():
            return False
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime_ns)
        if current == previous:
            stable_count += 1
            if stable_count >= STABLE_POLLS:
                return True
        else:
            stable_count = 0
        previous = current
        time.sleep(STABLE_POLL_INTERVAL_SECONDS)
    return False
