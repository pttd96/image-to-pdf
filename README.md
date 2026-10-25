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
- 📐 **Architecture Diagrams**: [word_pdf_pipeline.drawio](docs/word_pdf_pipeline.drawio) (compatible with Draw.io / diagrams.net) and [word_pdf_pipeline.excalidraw](docs/word_pdf_pipeline.excalidraw) (compatible with Excalidraw).
- 📋 **[ADR-001: Initial Core Architecture](docs/ADR-001.md)**: Cloud Vision + FastAPI + `fpdf2` foundational decisions.
- 📋 **[ADR-002: Evaluation of Digitization Paradigms](docs/ADR-002.md)**: Empirical findings, root cause analysis, and conclusions across all experimental approaches.

**Core Stack:**
- **Backend:** Python + FastAPI (Vercel serverless / Cloud Run)
- **Extraction:** Google Cloud Vision API (`DOCUMENT_TEXT_DETECTION`) + Gemini Flash 2.0 Multimodal
- **PDF Engine:** `fpdf2` with TrueType Unicode Roboto typography
- **Security:** GCP Workload Identity Federation (keyless OIDC) + optional reCAPTCHA v3

## Local Development & GCP Testing

### 1. Connect Locally to GCP

You can connect to Google Cloud using any of the following:

- **Option A (Recommended & Fastest):** Use Application Default Credentials:
  ```bash
  gcloud auth application-default login
  ```
- **Option B (Service Account File):** Download your GCP service account JSON key and set in `.env`:
  ```bash
  GOOGLE_APPLICATION_CREDENTIALS="./service-account.json"
  ```
- **Option C (Gemini API Key only):** Set in `.env`:
  ```bash
  GEMINI_API_KEY="AIzaSy..."
  ```

### 2. Verify Your Connection

Run the diagnostic test script:

```bash
python test_local_gcp.py
```

This verifies both Gemini Flash API and Google Cloud Vision API with real-time test requests and latency measurements.

### 3. Run the App Locally

```bash
uvicorn api.index:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## Cost

| Monthly Usage | Estimated Cost |
|---|---|
| < 1,000 images | **Free** (Google Vision free tier) |
| 1,000 – 5,000,000 images | $1.50 / 1,000 images |

Personal use is effectively **$0/month**.

## License

MIT
