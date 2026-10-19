import asyncio
import io
import os
import tempfile
from typing import List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from google.auth import identity_pool

from ocr import run_ocr_parallel
from pdf import build_pdf

load_dotenv()

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

MAX_IMAGES = 10
MAX_IMAGE_SIZE_MB = 10
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def get_gcp_credentials():
    """
    Load GCP credentials via Workload Identity Federation.

    In production (Vercel): exchanges VERCEL_OIDC_TOKEN for short-lived GCP credentials.
    In local dev: falls back to GOOGLE_CREDENTIALS_JSON (JSON string of service account key).
    """
    oidc_token = os.getenv("VERCEL_OIDC_TOKEN")

    if oidc_token:
        # --- Vercel production: use Workload Identity ---
        provider = os.environ["GCP_WORKLOAD_IDENTITY_PROVIDER"]
        sa_email = os.environ["GCP_SERVICE_ACCOUNT"]

        # google-auth's identity_pool requires a file or URL source.
        # Write the short-lived OIDC token to a temp file for this request.
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
    else:
        # --- Local dev: use JSON service account key ---
        import json
        from google.oauth2 import service_account

        raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if not raw:
            raise RuntimeError(
                "Neither VERCEL_OIDC_TOKEN nor GOOGLE_CREDENTIALS_JSON is set. "
                "Set GOOGLE_CREDENTIALS_JSON in your local .env file."
            )
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )


app = FastAPI(title="Image to PDF API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten to your Vercel domain in production
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


async def verify_recaptcha(token: str) -> bool:
    """Verify reCAPTCHA v3 token. Returns True if score >= 0.5."""
    if not RECAPTCHA_SECRET:
        return True  # Skip verification in local dev if secret not set
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            RECAPTCHA_VERIFY_URL,
            data={"secret": RECAPTCHA_SECRET, "response": token},
        )
        data = resp.json()
        return data.get("success", False) and data.get("score", 0) >= 0.5


@app.post("/api/convert", summary="Convert images to a searchable PDF")
async def convert(
    request: Request,
    images: List[UploadFile] = File(..., description="Images to convert (max 10)"),
    recaptcha_token: str = Form(..., description="reCAPTCHA v3 token"),
):
    # --- Validate reCAPTCHA ---
    if not await verify_recaptcha(recaptcha_token):
        raise HTTPException(status_code=403, detail="reCAPTCHA verification failed.")

    # --- Validate image count ---
    if len(images) == 0:
        raise HTTPException(status_code=400, detail="At least one image is required.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed.")

    # --- Read and validate each image ---
    image_bytes_list: List[bytes] = []
    for img in images:
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

    # --- Load GCP credentials and run OCR in parallel ---
    credentials = get_gcp_credentials()
    ocr_results = await run_ocr_parallel(image_bytes_list, credentials)

    # --- Build multi-page PDF ---
    pdf_bytes = build_pdf(image_bytes_list, ocr_results)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=document.pdf"},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}
