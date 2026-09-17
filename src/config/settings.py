"""Application settings and thresholds.

Single source of truth for configuration. Values are read from environment
variables once, at import time, with sensible defaults for local development.
"""

from __future__ import annotations

import os

# --- Runtime data directories -------------------------------------------------
INBOX_DIR = os.getenv("GRADING_INBOX_DIR", "inbox")
OUTBOX_DIR = os.getenv("GRADING_OUTBOX_DIR", "outbox")
RUBRICS_DIR = os.getenv("GRADING_RUBRICS_DIR", "rubrics")

# --- OCR: preprocessing -------------------------------------------------------
# DPI used when rendering PDF pages to images. Higher DPI gives the vision model
# sharper input, which improves both diagram bounding-box accuracy (thin arrows
# and pencil lines are detected rather than missed) and the resolution of the
# saved crop. 300 is a good default; drop to 150 for faster throughput.
PDF_RENDER_DPI = int(os.getenv("OCR_PDF_DPI", "300"))

# Cap the longest page width (px). The whole-page image is sent to the vision
# model AND is the source the diagram crop is taken from, so it trades speed/cost
# vs accuracy: lower (1280) is faster and cheaper, higher (2048) lets the model
# localize fine features (thin force arrows) and produces sharper crops. 2048 is
# a good balance for pages with diagrams.
MAX_IMAGE_WIDTH = int(os.getenv("OCR_MAX_IMAGE_WIDTH", "2048"))

# Format used to encode preserved diagram crops.
IMAGE_CROP_FORMAT = os.getenv("OCR_IMAGE_CROP_FORMAT", "PNG")

# Diagram crops are padded outward before saving. Vision models (Gemini) tend to
# return tight bounding boxes that clip thin features at the edges — e.g. a
# downward weight arrow on a free-body diagram — which silently corrupts the
# evaluation. Padding = a fraction of each box dimension, floored at an absolute
# pixel minimum so small boxes still get a usable margin. Clamped to page bounds.
DIAGRAM_CROP_PADDING_RATIO = float(os.getenv("OCR_DIAGRAM_CROP_PADDING_RATIO", "0.08"))
DIAGRAM_CROP_PADDING_MIN_PX = int(os.getenv("OCR_DIAGRAM_CROP_PADDING_MIN_PX", "30"))

# --- OCR: text recognition ---------------------------------------------------
# OCR engine: each sends the whole page to a vision model in one request.
# Layout detection, transcription, and diagram localization happen in that
# single call — no separate layout stage.
#   "gemini" — Google Gemini 2.5 Flash (default). Needs GEMINI_API_KEY.
#              Best diagram bounding-box precision.
#   "azure"  — Azure OpenAI GPT-4.1 (fallback). Needs AZURE_OPENAI_* vars.
OCR_ENGINE = os.getenv("OCR_ENGINE", "gemini").strip().lower()

# --- Gemini provider ---------------------------------------------------------
# API key for the Gemini path (AI Studio key). Only required when OCR_ENGINE
# (or any Gemini-backed client) is used; validated fast-fail at client init.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Free-tier multimodal model used for page OCR + diagram localization.
# gemini-2.5-flash is the best all-rounder; gemini-2.5-flash-lite / gemini-2.0-flash
# are lighter if you hit rate limits.
GEMINI_OCR_MODEL = os.getenv("GEMINI_OCR_MODEL", "gemini-2.5-flash")

# --- OCR: question grouping ---------------------------------------------------
# Detects the start of a new question at the beginning of an OCR'd block and
# captures its number. Designed to generalize across homework/assignment styles
# rather than a single template. Two branches:
#   1. Keyword form — an optional label (Q, Qn, Que, Question, Ex, Exercise,
#      Problem, Prob, Part) + number, with the trailing separator OPTIONAL. The
#      keyword disambiguates, so "Q10", "Q10.", "Q10)", "Question 3", "Problem 5"
#      and OCR-mangled "Q10List" / "Q7/," all match.
#   2. Bare form — a number that MUST be followed by a separator (. ) : - ]),
#      e.g. "1.", "2)", "3:". The separator is required here so ordinary prose
#      that merely starts with a digit is not mistaken for a question.
# The number is captured by whichever branch matches (see _marker_number()).
QUESTION_MARKER_PATTERN = os.getenv(
    "OCR_QUESTION_MARKER_PATTERN",
    r"^\s*(?:"
    r"(?:Q(?:uestion|ues|n)?|Ex(?:ercise)?|Problem|Prob|Part)\s*[.\-:]?\s*"
    r"(\d{1,3})[.\)\:\-\]/,]?"
    r"|"
    r"(\d{1,3})\s*[.\)\:\-\]]"
    r")",
)

# Layout/OCR blocks with confidence below this are logged and flagged for review.
MIN_OCR_CONFIDENCE = float(os.getenv("OCR_MIN_CONFIDENCE", "0.5"))

# --- Evaluation provider ------------------------------------------------------
# Which LLM/vision provider to use for grading: "gemini" or "azure".
EVAL_PROVIDER = os.getenv("EVAL_PROVIDER", "azure").strip().lower()

# --- Watcher idempotency ------------------------------------------------------
# Whether the watcher should automatically retry unchanged submissions that
# already ended in "failed" state on startup. Default false to avoid restart
# loops; set true if you want automatic retries after transient outages.
REPROCESS_FAILED_SUBMISSIONS = (
    os.getenv("WATCHER_REPROCESS_FAILED_SUBMISSIONS", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

# --- Azure OpenAI provider ----------------------------------------------------
# Used by evaluation, report generation, and feedback generation. A single
# GPT-4o/4.1 deployment handles both text completions and vision (multimodal)
# calls. Fast-fail validation runs at client init — missing keys surface
# immediately.
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv(
    "AZURE_OPENAI_ENDPOINT", "https://ita-ai-internal.openai.azure.com/"
)
# Deployment name in your Azure Foundry resource (e.g. "gpt-4o", "gpt-4.1-mini").
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
# Max tokens in the model's response for a single evaluation call.
AZURE_MAX_TOKENS = int(os.getenv("AZURE_MAX_TOKENS", "5000"))
