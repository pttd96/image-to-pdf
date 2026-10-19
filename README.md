# 📄 Image to PDF

A lightweight web app that converts photos of documents (contracts, forms, letters) into searchable, multi-page PDFs — powered by Google Cloud Vision API.

## Features

- 📷 Upload multiple images (up to 10) — drag & drop supported
- 🔍 OCR via Google Cloud Vision API (Vietnamese + English)
- 📑 Merged into a single searchable PDF (image + invisible text layer)
- 📥 Instant download — no sign-up required
- 🛡️ Abuse protection: IP rate limiting + reCAPTCHA v3

## Project Structure

```
image-to-pdf/
├── backend/          # FastAPI Python backend
│   ├── main.py       # API entrypoint
│   ├── ocr.py        # Google Vision API integration
│   ├── pdf.py        # PDF generation (fpdf2)
│   └── requirements.txt
├── frontend/         # Static HTML/JS frontend (or Next.js)
│   ├── index.html
│   ├── app.js
│   └── style.css
└── docs/
    └── ADR-001.md    # Architecture Decision Record
```

## Architecture

See [ADR-001](docs/ADR-001.md) for full architecture decisions.

**Stack:**
- **Backend:** Python + FastAPI, hosted on Cloud Run
- **OCR:** Google Cloud Vision API (`DOCUMENT_TEXT_DETECTION`)
- **PDF:** `fpdf2` — image layer + invisible text layer
- **Rate limiting:** `slowapi` + reCAPTCHA v3

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set your Google Cloud credentials
export GOOGLE_APPLICATION_CREDENTIALS="path/to/service-account.json"
export RECAPTCHA_SECRET_KEY="your-recaptcha-secret"

uvicorn main:app --reload
```

### Frontend

Open `frontend/index.html` in a browser, or serve with any static server:

```bash
cd frontend
npx serve .
```

## Cost

| Monthly Usage | Estimated Cost |
|---|---|
| < 1,000 images | **Free** (Google Vision free tier) |
| 1,000 – 5,000,000 images | $1.50 / 1,000 images |

Personal use is effectively **$0/month**.

## License

MIT
