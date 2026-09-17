# intel-innovation-automated-assignment-grading

## Quick Start

### Prerequisites
- Python 3.12+, virtual environment
- Node.js 18+ (for UI)
- API key: `GEMINI_API_KEY` or `AZURE_OPENAI_API_KEY`

### 1. Install dependencies

```bash
# Backend (grading pipeline)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Server (API for UI)
pip install -r server/requirements.txt

# Frontend
cd ui && npm install && cd ..
```

### 2. Set environment variables

```bash
# OCR (pick one)
export OCR_ENGINE="gemini"              # or "azure" or "surya"
export GEMINI_API_KEY="your-key"        # required for gemini engine

# Evaluation provider (pick one)
export EVAL_PROVIDER="azure"            # or "gemini"

# Azure Foundry (if using azure)
export AZURE_OPENAI_API_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="azure foundry url"
export AZURE_OPENAI_DEPLOYMENT="gpt-4.1"
```

### 3. Run the OCR pipeline

```bash
# Verify OCR on a submission (writes debug output to outbox/ocr_debug/)
.venv/bin/python3 scripts/verify_ocr.py inbox/submission.pdf

# Dump OCR result as JSON for evaluation
.venv/bin/python3 scripts/verify_ocr.py inbox/submission.pdf --dump-json inbox/submission.json
```

### 4. Run the evaluator

```bash
# Grade a submission using OCR JSON output + rubric
.venv/bin/python3 scripts/process_ocr_output.py inbox/submission.json

# With explicit rubric file
.venv/bin/python3 scripts/process_ocr_output.py inbox/submission.json --rubric rubrics/physics.yaml
```

Output: `outbox/<name>_report.json` and `outbox/<name>_feedback.txt`

### 5. Run the agentic watcher (end-to-end)

```bash
# Continuous watch — monitors inbox/, auto-grades new submissions
.venv/bin/python3 -m src.watcher

# One-shot — process all existing inbox files and exit
.venv/bin/python3 -m src.watcher --once
```

The agent runs: `inbox/ → OCR → rubric → evaluation → report → feedback → outbox/`
- Retries transient failures (API 503s) with exponential backoff
- Flags low-confidence questions for teacher review
- Writes `outbox/<name>_status.json` with final state and any errors

### 6. Generate feedback from an existing report

```bash
.venv/bin/python3 scripts/generate_feedback.py outbox/submission_grading_report.json
```

### 7. Run the UI

```bash
# Terminal 1: Backend API (dummy responses)
cd server && uvicorn app:app --reload --port 8000

# Terminal 2: Frontend
cd ui && npm run dev
```

Open http://localhost:3000
- **Student:** Upload assignments, view scores
- **Teacher:** Review flagged submissions, override scores, release to students

---

## Architecture

1 Monitoring system (app skeleton)
watches for new uploads in directory
duplicate check — P2
2 OCR extraction
PDF → image handling (split, format validation)
text extraction
image extraction
preserve Q/A pair context
3 Rubric / answer key management ← NEW
load answer key / grading criteria
map rubric to submission
4 Answer evaluation
parallel processing of Q/A pairs
check for backreferencing, move to sequential if found
map Q/A to evaluation
route by question type: text → text eval, diagram → vision eval
add system prompt
5 Report generator
add system prompt
summarize Q/A + evaluations (scores, rubric mapping)
integrate with AZ Foundry client
6 Feedback system
add system prompt
generate student-facing narrative feedback
7 Azure Foundry client ← EXTRACTED (shared LLM utility)
used by Tasks 4, 5, 6
8 Caching — P2 — DB
status of execution
hashed order-preserving Q/A pairs
evaluation result
feedback
9 Client module
UI (prioritized over CLI)
server module
student chat — Telegram (P2)
