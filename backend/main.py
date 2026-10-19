import asyncio
import io
import os
from contextlib import asynccontextmanager
from typing import List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from ocr import run_ocr_parallel
from pdf import build_pdf

load_dotenv()

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

MAX_IMAGES = 10
MAX_IMAGE_SIZE_MB = 10
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

limiter = Limiter(key_func=get_remote_address, default_limits=["10/day", "1/5seconds"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Image to PDF API", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production to your frontend domain
    allow_methods=["POST"],
    allow_headers=["*"],
)


async def verify_recaptcha(token: str) -> bool:
    """Verify reCAPTCHA v3 token. Returns True if score >= 0.5."""
    if not RECAPTCHA_SECRET:
        return True  # Skip in local dev if secret not set
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            RECAPTCHA_VERIFY_URL,
            data={"secret": RECAPTCHA_SECRET, "response": token},
        )
        data = resp.json()
        return data.get("success", False) and data.get("score", 0) >= 0.5


@app.post("/convert", summary="Convert images to a searchable PDF")
@limiter.limit("10/day")
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
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images per request.")

    # --- Read and validate each image ---
    image_bytes_list: List[bytes] = []
    for img in images:
        if img.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type: {img.content_type}. Use JPEG, PNG, or WebP.",
            )
        data = await img.read()
        if len(data) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"Image '{img.filename}' exceeds {MAX_IMAGE_SIZE_MB}MB limit.",
            )
        image_bytes_list.append(data)

    # --- Run OCR on all images in parallel ---
    ocr_results = await run_ocr_parallel(image_bytes_list)

    # --- Build multi-page PDF ---
    pdf_bytes = build_pdf(image_bytes_list, ocr_results)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=document.pdf"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
