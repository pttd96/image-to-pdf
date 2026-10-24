# 📄 Image to PDF

A lightweight web app that converts photos of documents (contracts, forms, letters) into searchable, multi-page PDFs — powered by Google Cloud Vision API.

## Features

- 📷 Upload multiple images (up to 10) — drag & drop supported
- 📊 Native table & layout extraction powered by Gemini Flash (detects headers, columns, cells, and key-values)
- 🔍 OCR fallback via Google Cloud Vision API (Vietnamese + English)
- 📑 Reconstructed into a clean digitized PDF with Unicode TrueType typography (Roboto)
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

## Architecture & Documentation

Comprehensive system architecture and research documentation:
- 🏛️ **[System Architecture & Digitization Wiki](docs/ARCHITECTURE.md)**: In-depth technical breakdown of the system architecture, evaluation of the 4 document digitization approaches tested, comparison matrix, and future computer-vision dewarping roadmap.
- 📋 **[ADR-001: Initial Core Architecture](docs/ADR-001.md)**: Cloud Vision + FastAPI + `fpdf2` foundational decisions.
- 📋 **[ADR-002: Evaluation of Digitization Paradigms](docs/ADR-002.md)**: Empirical findings, root cause analysis, and conclusions across all experimental approaches.

**Core Stack:**
- **Backend:** Python + FastAPI (Vercel serverless / Cloud Run)
- **Extraction:** Google Cloud Vision API (`DOCUMENT_TEXT_DETECTION`) + Gemini Flash 2.0 Multimodal
- **PDF Engine:** `fpdf2` with TrueType Unicode Roboto typography
- **Security:** GCP Workload Identity Federation (keyless OIDC) + optional reCAPTCHA v3

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set credentials (GEMINI_API_KEY for tables + structured layout, or GCP for Vision OCR)
export GEMINI_API_KEY="your-gemini-api-key"
export GOOGLE_APPLICATION_CREDENTIALS="path/to/service-account.json"
export RECAPTCHA_SECRET_KEY="your-recaptcha-secret"

uvicorn api.index:app --reload
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
