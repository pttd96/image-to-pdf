import asyncio
import base64
import json
import os
from typing import Any, List, Optional, Union

import httpx
from google.cloud import vision
from google.oauth2.service_account import Credentials

try:
    from models import PageDocument
except ImportError:
    from api.models import PageDocument


GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-2.5-flash"]
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

GEMINI_JSON_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "blocks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {
                        "type": "STRING",
                        "enum": ["heading", "paragraph", "table", "key_value"],
                    },
                    "text": {"type": "STRING"},
                    "level": {"type": "INTEGER"},
                    "align": {"type": "STRING", "enum": ["left", "center", "right"]},
                    "title": {"type": "STRING"},
                    "headers": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "rows": {
                        "type": "ARRAY",
                        "items": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                        },
                    },
                    "label": {"type": "STRING"},
                    "value": {"type": "STRING"},
                },
                "required": ["type"],
            },
        }
    },
    "required": ["blocks"],
}

SYSTEM_INSTRUCTION = (
    "You are an expert document and book digitizer. Analyze this physical document/book image "
    "(Vietnamese & English). Extract the content in exact vertical reading order into structured blocks.\n"
    "- If a table or grid is present, extract it as a 'table' block with all header columns and row values. "
    "Do not miss table cells or borders.\n"
    "- For document titles, book chapter headings, mottos (e.g. 'CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM', 'HỢP ĐỒNG LAO ĐỘNG', 'Chương 1: ...'), "
    "or section/article headings (e.g. 'Điều 1: ...', '1.1 Khái niệm'), extract as 'heading' with appropriate "
    "level (1 for main chapter/document titles, 2 for sections/articles, 3 for subsections) and align ('center' for mottos/titles, 'left' for sections).\n"
    "- For labeled form items (e.g. 'Họ và tên: Nguyễn Văn A', 'Địa chỉ: ...'), extract as 'key_value'.\n"
    "- For all standard text, book body paragraphs, contract clauses, and descriptions, extract as 'paragraph'. "
    "IMPORTANT: In printed books and documents, physical lines are wrapped to the page width. You MUST merge wrapped lines "
    "within the same sentence or paragraph into a continuous flowing block of text. Do NOT leave artificial line breaks in the "
    "middle of sentences. Only split into separate paragraph blocks when there is a genuine paragraph break in the source text.\n"
    "Preserve exact Vietnamese diacritics, numbers, and spelling. Output strictly valid JSON conforming to the schema."
)


def _detect_mime_type(image_bytes: bytes) -> str:
    """Detects image MIME type from initial bytes."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def _extract_with_gemini(
    client: httpx.AsyncClient,
    image_bytes: bytes,
    api_key: str,
) -> Optional[PageDocument]:
    """Calls Gemini Flash API with structured JSON output to extract document layout and tables."""
    mime_type = _detect_mime_type(image_bytes)
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_INSTRUCTION},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": GEMINI_JSON_SCHEMA,
            "temperature": 0.1,
        },
    }

    # Try preferred model first, then fallback model
    last_error = None
    for model in GEMINI_MODELS:
        url = GEMINI_API_URL.format(model=model)
        try:
            resp = await client.post(
                url,
                params={"key": api_key},
                json=payload,
                timeout=45.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return None
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    return None
                text_content = parts[0].get("text", "")
                if not text_content:
                    return None

                return PageDocument.model_validate_json(text_content)
            elif resp.status_code == 402:
                last_error = (
                    f"🚨 [BILLING ALERT] Gemini credits depleted (HTTP 402)! "
                    f"Table extraction disabled. Recharge at https://aistudio.google.com"
                )
                print(f"\n{last_error}\n")
                break
            elif resp.status_code in (401, 403):
                last_error = f"🚨 [AUTH ERROR] Gemini API key invalid (HTTP {resp.status_code}): {resp.text[:150]}"
                print(f"\n{last_error}\n")
                break
            else:
                last_error = f"Gemini ({model}) HTTP {resp.status_code}: {resp.text[:200]}"
                print(f"[OCR] {last_error}")
                if resp.status_code == 404:
                    continue
        except Exception as e:
            last_error = f"Gemini ({model}) request error: {e}"
            print(f"[OCR] {last_error}")
            continue

    if last_error:
        print(f"[OCR] Gemini structured extraction failed: {last_error}")
    return None


def _ocr_single_vision(client: vision.ImageAnnotatorClient, image_bytes: bytes) -> Optional[str]:
    """Runs Cloud Vision DOCUMENT_TEXT_DETECTION on a single image. Returns extracted plain text or None."""
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")
    annotation = response.full_text_annotation
    return annotation.text if annotation else None


async def extract_document_parallel(
    image_bytes_list: List[bytes],
    credentials: Optional[Credentials] = None,
    gemini_api_key: Optional[str] = None,
) -> List[Union[PageDocument, str, None]]:
    """
    Extracts structured document content (with table parsing) from multiple images concurrently.

    1. If `GEMINI_API_KEY` is present, uses Gemini Flash for structured JSON extraction
       (detecting tables, headings, paragraphs, key-values).
    2. If Gemini is unavailable, errors, or is not configured, gracefully falls back to
       Google Cloud Vision API linear OCR.
    """
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "").strip()

    if api_key:
        print(f"[OCR] Using Gemini Flash for structured document extraction ({len(image_bytes_list)} images)...")
        async with httpx.AsyncClient() as http_client:
            tasks = [
                _extract_with_gemini(http_client, img_bytes, api_key)
                for img_bytes in image_bytes_list
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        parsed_results: List[Union[PageDocument, str, None]] = []
        fallback_needed_indices: List[int] = []

        for i, res in enumerate(results):
            if isinstance(res, PageDocument) and res.blocks:
                parsed_results.append(res)
            else:
                print(f"[OCR] Gemini extraction returned empty/error for image {i+1}, marking for fallback...")
                parsed_results.append(None)
                fallback_needed_indices.append(i)

        # If all succeeded, return immediately
        if not fallback_needed_indices:
            print("[OCR] All images successfully extracted with Gemini structured parser.")
            return parsed_results

        # Fallback to Vision API for images that failed Gemini
        if credentials:
            print(f"[OCR] Running Cloud Vision fallback for {len(fallback_needed_indices)} images...")
            vision_client = vision.ImageAnnotatorClient(credentials=credentials)
            loop = asyncio.get_event_loop()
            fb_tasks = [
                loop.run_in_executor(None, _ocr_single_vision, vision_client, image_bytes_list[idx])
                for idx in fallback_needed_indices
            ]
            fb_results = await asyncio.gather(*fb_tasks, return_exceptions=True)
            for idx, fb_res in zip(fallback_needed_indices, fb_results):
                if isinstance(fb_res, str):
                    parsed_results[idx] = fb_res
                else:
                    print(f"[OCR] Vision fallback also failed for image {idx+1}: {fb_res}")

        return parsed_results

    # If no Gemini key, use Cloud Vision directly
    print(f"[OCR] GEMINI_API_KEY not configured. Running Cloud Vision OCR for {len(image_bytes_list)} images...")
    if not credentials:
        raise RuntimeError("Neither GEMINI_API_KEY nor GCP credentials are provided for document extraction.")

    vision_client = vision.ImageAnnotatorClient(credentials=credentials)
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _ocr_single_vision, vision_client, img_bytes)
        for img_bytes in image_bytes_list
    ]
    raw_results = await asyncio.gather(*tasks)
    return list(raw_results)


async def run_ocr_parallel(
    image_bytes_list: List[bytes],
    credentials: Credentials,
) -> List[Optional[str]]:
    """Legacy helper: Runs plain Cloud Vision OCR on all images concurrently."""
    client = vision.ImageAnnotatorClient(credentials=credentials)
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _ocr_single_vision, client, img_bytes)
        for img_bytes in image_bytes_list
    ]
    results = await asyncio.gather(*tasks)
    return list(results)
