"""Rubric loader utilities.

Loads rubric files from JSON or YAML and converts them into the evaluator's
``dict[int, str]`` mapping from question id to grading criteria text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import settings


def parse_rubric_file(path: str | Path) -> dict[int, str]:
    """Load one rubric file from JSON or YAML into a q_id -> criteria mapping."""
    rubric_path = Path(path)
    raw = rubric_path.read_text(encoding="utf-8")
    suffix = rubric_path.suffix.lower()

    if suffix == ".json":
        data = json.loads(raw)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "PyYAML is required to load YAML rubrics. Install it with `pip install PyYAML`."
            ) from exc
        data = yaml.safe_load(raw)
    else:
        raise ValueError(f"Unsupported rubric file type: {rubric_path.suffix}")

    return _normalize_rubric(data, rubric_path)


def load_rubric_for_submission(
    source_path: str | Path,
    rubrics_dir: str | Path | None = None,
) -> dict[int, str]:
    """Find the best matching rubric for a submission path and load it."""
    source = Path(source_path)
    rubric_dir = Path(rubrics_dir or settings.RUBRICS_DIR)
    candidates = sorted(
        path
        for path in rubric_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml"}
    )
    if not candidates:
        raise FileNotFoundError(f"No rubric files found in {rubric_dir}")

    stem_matches = [path for path in candidates if path.stem == source.stem]
    if stem_matches:
        return parse_rubric_file(stem_matches[0])
    if len(candidates) == 1:
        return parse_rubric_file(candidates[0])

    raise FileNotFoundError(
        f"No rubric matched {source.name}. Available rubrics: "
        + ", ".join(path.name for path in candidates)
    )


def _normalize_rubric(data: Any, source: Path) -> dict[int, str]:
    if not isinstance(data, dict):
        raise ValueError(f"Rubric file must contain an object at top level: {source}")

    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError(f"Rubric file must contain a 'questions' list: {source}")

    rubric: dict[int, str] = {}
    for item in questions:
        if not isinstance(item, dict):
            raise ValueError(f"Rubric entries must be objects: {source}")
        q_id = int(item["id"])
        criteria = str(item.get("criteria", "")).strip()
        max_score = item.get("max_score")
        if max_score is not None:
            criteria = f"{criteria}\n\nMaximum score: {max_score}"
        rubric[q_id] = criteria
    return rubric