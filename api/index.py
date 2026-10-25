import asyncio
import io
import os
import tempfile
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from google.auth import identity_pool

import sys
from pathlib import Path

# Ensure api directory is in Python path for Vercel serverless execution
sys.path.insert(0, str(Path(__file__).parent))

try:
    from ocr import extract_document_parallel, run_ocr_parallel
    from pdf import build_pdf
    from dewarp import rectify_document_bytes
    from transcribe import transcribe_image_to_markdown
    from word_pdf import build_word_style_pdf
except ImportError:
    from api.ocr import extract_document_parallel, run_ocr_parallel
    from api.pdf import build_pdf
    from api.dewarp import rectify_document_bytes
    from api.transcribe import transcribe_image_to_markdown
    from api.word_pdf import build_word_style_pdf

load_dotenv()

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

MAX_IMAGES = 10
MAX_IMAGE_SIZE_MB = 10
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def get_gcp_credentials(request: Request = None):
    """
    Load GCP credentials supporting:
    1. Vercel Production: Workload Identity Federation via OIDC token
    2. Local .env: Service account JSON string in GOOGLE_CREDENTIALS_JSON
    3. Local File: Service account key file path in GOOGLE_APPLICATION_CREDENTIALS
    4. Local CLI: gcloud auth application-default login (Application Default Credentials)
    """
    oidc_token = None
    if request:
        oidc_token = request.headers.get("x-vercel-oidc-token")
    if not oidc_token:
        oidc_token = os.getenv("VERCEL_OIDC_TOKEN")

    if oidc_token:
        # --- Vercel production: use Workload Identity Federation ---
        provider = os.getenv("GCP_WORKLOAD_IDENTITY_PROVIDER", "").strip()
        sa_email = os.getenv("GCP_SERVICE_ACCOUNT", "").strip()

        print(f"[AUTH] Workload Identity: Provider={provider or 'MISSING'}, SA={sa_email or 'MISSING'}")

        if not provider:
            raise RuntimeError("Environment variable 'GCP_WORKLOAD_IDENTITY_PROVIDER' is not set.")
        if not sa_email:
            raise RuntimeError("Environment variable 'GCP_SERVICE_ACCOUNT' is not set.")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(oidc_token)
            token_file = f.name

        return identity_pool.Credentials.from_info(
            {
                "type": "external_account",
                "audience": f"//iam.googleapis.com/{provider}",
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                "token_url": "https://sts.googleapis.com/v1/token",
                "service_account_impersonation_url": (
                    f"https://iamcredentials.googleapis.com/v1/projects/-"
                    f"/serviceAccounts/{sa_email}:generateAccessToken"
                ),
                "credential_source": {"file": token_file},
            }
        )

    # --- Local Option A: JSON string in GOOGLE_CREDENTIALS_JSON ---
    raw_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if raw_json:
        import json
        from google.oauth2 import service_account
        info = json.loads(raw_json)
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    # --- Local Option B: File path in GOOGLE_APPLICATION_CREDENTIALS ---
    cred_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if cred_file and os.path.isfile(cred_file):
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(
            cred_file,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    # --- Local Option C: gcloud auth application-default login (ADC) ---
    try:
        import google.auth
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return credentials
    except Exception as adc_err:
        raise RuntimeError(
            "No GCP credentials found for local execution.\n"
            "Choose one of the following methods to connect:\n"
            "  1. Run: gcloud auth application-default login (Easiest)\n"
            "  2. Set GOOGLE_APPLICATION_CREDENTIALS='path/to/key.json' in .env\n"
            "  3. Set GOOGLE_CREDENTIALS_JSON='{...}' in .env"
        ) from adc_err


app = FastAPI(title="Image to PDF API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_recaptcha(token: str) -> bool:
    """Verify reCAPTCHA v3 token. Returns True if score >= 0.5."""
    if not RECAPTCHA_SECRET:
        return True
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            RECAPTCHA_VERIFY_URL,
            data={"secret": RECAPTCHA_SECRET, "response": token},
        )
        data = resp.json()
        return data.get("success", False) and data.get("score", 0) >= 0.5


@app.post("/api/convert", summary="Convert images to a searchable PDF")
@app.post("/convert", summary="Convert images to a searchable PDF (alias)")
@app.post("/api/index/convert")
@app.post("/api/index")
@app.post("/")
async def convert(
    request: Request,
    images: List[UploadFile] = File(..., description="Images to convert (max 10)"),
    recaptcha_token: Optional[str] = Form(None, description="reCAPTCHA v3 token"),
):
    try:
        print(f"[CONVERT] Starting conversion for {len(images)} images...")

        # --- Validate reCAPTCHA (skipped if RECAPTCHA_SECRET_KEY is not set) ---
        if RECAPTCHA_SECRET:
            if not recaptcha_token or not await verify_recaptcha(recaptcha_token):
                raise HTTPException(status_code=403, detail="reCAPTCHA verification failed.")

        # --- Validate image count ---
        if len(images) == 0:
            raise HTTPException(status_code=400, detail="At least one image is required.")
        if len(images) > MAX_IMAGES:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed.")

        # --- Read and validate each image ---
        image_bytes_list: List[bytes] = []
        for i, img in enumerate(images):
            print(f"[CONVERT] Processing image {i+1}: {img.filename} ({img.content_type})")
            if img.content_type not in ALLOWED_CONTENT_TYPES:
                raise HTTPException(
                    status_code=415,
                    detail=f"Unsupported type: {img.content_type}. Use JPEG, PNG, or WebP.",
                )
            data = await img.read()
            if len(data) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail=f"'{img.filename}' exceeds {MAX_IMAGE_SIZE_MB}MB limit.",
                )
            image_bytes_list.append(data)

        # --- Load credentials and run extraction in parallel ---
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        credentials = None
        try:
            credentials = get_gcp_credentials(request)
        except Exception as auth_err:
            print(f"[AUTH] GCP credentials not available: {auth_err}")

        if not gemini_key and not credentials:
            raise HTTPException(
                status_code=500,
                detail="No OCR or extraction credentials configured. Please set GEMINI_API_KEY or GCP credentials.",
            )

        # --- Step 1: Geometric Rectification (OpenCV homography & deskew) ---
        print("[CONVERT] Rectifying images (OpenCV perspective homography & deskew)...")
        rectified_images = [rectify_document_bytes(img_bytes) for img_bytes in image_bytes_list]

        # --- Step 2 & 3: Word-Style Transcription with Gemini Flash ---
        if gemini_key:
            print("[CONVERT] Transcribing documents to Word-Style Markdown via Gemini Flash...")
            async with httpx.AsyncClient() as http_client:
                tasks = [
                    transcribe_image_to_markdown(http_client, img, gemini_key)
                    for img in rectified_images
                ]
                markdown_pages = await asyncio.gather(*tasks)

            if any(md.strip() for md in markdown_pages if md):
                print(f"[CONVERT] Assembling Word-Grade vector PDF ({len(markdown_pages)} pages)...")
                pdf_bytes = build_word_style_pdf(markdown_pages)
                print(f"[CONVERT] Word-Grade PDF built successfully ({len(pdf_bytes)} bytes)")
                return Response(
                    content=pdf_bytes,
                    media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=document.pdf"},
                )

        # --- Fallback: Structured Typesetter via Cloud Vision / JSON blocks ---
        print("[CONVERT] Fallback: Running Cloud Vision / legacy extraction...")
        extraction_results = await extract_document_parallel(
            image_bytes_list=rectified_images,
            credentials=credentials,
            gemini_api_key=gemini_key,
        )
        print(f"[CONVERT] Extraction finished. Results count: {len(extraction_results)}")

        print("[CONVERT] Building PDF with fpdf2...")
        pdf_bytes = build_pdf(rectified_images, extraction_results)
        print(f"[CONVERT] PDF built successfully ({len(pdf_bytes)} bytes)")

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=document.pdf"},
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"[CONVERT ERROR] {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/api/health")
@app.get("/health")
@app.get("/api/index/health")
@app.get("/api/index")
async def health():
    return {"status": "ok"}


# Serve static web UI files when running locally
from fastapi.staticfiles import StaticFiles

public_dir = Path(__file__).parent.parent / "public"
if public_dir.exists():
    app.mount("/", StaticFiles(directory=str(public_dir), html=True), name="public")
