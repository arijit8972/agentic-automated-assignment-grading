# OCR Module

Two sequential stages: `preprocessor → extractor`

| File | Stage | Input | Output |
|------|-------|-------|--------|
| `preprocessor.py` | 1 | PDF / image file | Ordered list of normalized page images |
| `extractor.py` | 2 | Page images | `ExtractedSubmission` — text + diagram crops |

Layout detection, OCR, and diagram localization are all performed by the
extractor in a single cloud vision call per page — there is no separate layout
stage.

Shared models: `src/models/submission.py` (`ExtractedQuestion`, `ExtractedSubmission`).
Configuration: `src/config/settings.py`.

---

## Engines

Set `OCR_ENGINE` to select the provider:

| Value | Provider | Requires |
|---|---|---|
| `gemini` (default) | Google Gemini 2.5 Flash | `GEMINI_API_KEY` |
| `azure` | Azure OpenAI GPT-4.1 | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` |

The Gemini free tier is the primary engine. Azure is the fallback when Gemini
quota is exhausted.

```bash
# Gemini (default)
export GEMINI_API_KEY=<your-ai-studio-key>
python scripts/verify_ocr.py inbox/physics_question_paper.pdf

# Azure fallback
export OCR_ENGINE=azure
export AZURE_OPENAI_API_KEY=<key>
python scripts/verify_ocr.py inbox/physics_question_paper.pdf
```

---

## Tuning knobs

| Variable | Default | Effect |
|---|---|---|
| `OCR_PDF_DPI` | `192` | PDF render DPI — lower (96) for speed, higher (300) for accuracy |
| `OCR_MAX_IMAGE_WIDTH` | `1280` | Page width cap in pixels |
| `GEMINI_OCR_MODEL` | `gemini-2.5-flash` | Gemini model; try `gemini-2.5-flash-lite` or `gemini-2.0-flash` if quota is hit |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4.1` | Azure deployment name |
| `AZURE_MAX_TOKENS` | `5000` | Max response tokens for the OCR call |
| `OCR_MIN_CONFIDENCE` | `0.5` | Blocks below this confidence are flagged for human review |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose per-block output |
