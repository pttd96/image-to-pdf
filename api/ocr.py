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


GEMINI_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash"]
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
            else:
                last_error = f"Gemini ({model}) HTTP {resp.status_code}: {resp.text[:200]}"
                print(f"[OCR] {last_error}")
                # If 404 or model not found, try next model
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


def _ocr_single_vision_boxes(client: vision.ImageAnnotatorClient, image_bytes: bytes) -> dict:
    """Extracts exact word bounding boxes and page dimensions from Cloud Vision OCR."""
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")

    annotation = response.full_text_annotation
    if not annotation:
        return {"words": [], "width": 0, "height": 0, "full_text": ""}

    words_data = []
    page_w = 0
    page_h = 0

    if annotation.pages:
        p = annotation.pages[0]
        page_w = p.width
        page_h = p.height
        for block in p.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    word_str = "".join(s.text for s in word.symbols).strip()
                    if not word_str:
                        continue
                    verts = word.bounding_box.vertices
                    xs = [v.x for v in verts if hasattr(v, "x")]
                    ys = [v.y for v in verts if hasattr(v, "y")]
                    if not xs or not ys:
                        continue
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    words_data.append({
                        "text": word_str,
                        "x": min_x,
                        "y": min_y,
                        "w": max(1, max_x - min_x),
                        "h": max(1, max_y - min_y),
                    })

    return {
        "words": words_data,
        "width": page_w,
        "height": page_h,
        "full_text": annotation.text,
    }


GEMINI_BOX_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "words": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "box_2d": {
                        "type": "ARRAY",
                        "items": {"type": "INTEGER"},
                        "description": "[ymin, xmin, ymax, xmax] normalized to 1000",
                    },
                },
                "required": ["text", "box_2d"],
            },
        }
    },
    "required": ["words"],
}


async def _extract_boxes_with_gemini(
    client: httpx.AsyncClient,
    image_bytes: bytes,
    api_key: str,
) -> dict:
    """Extracts bounding boxes from Gemini Flash when Cloud Vision is not configured."""
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = img.size
    mime_type = _detect_mime_type(image_bytes)
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "Detect all visible words or text tokens on this page with their bounding box. "
        "Return strictly JSON with 'words': [{'text': '...', 'box_2d': [ymin, xmin, ymax, xmax]}]. "
        "Coordinates must be normalized from 0 to 1000."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": GEMINI_BOX_SCHEMA,
            "temperature": 0.1,
        },
    }

    for model in GEMINI_MODELS:
        url = GEMINI_API_URL.format(model=model)
        try:
            resp = await client.post(url, params={"key": api_key}, json=payload, timeout=45.0)
            if resp.status_code == 200:
                data = resp.json()
                text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text_content)
                words_list = []
                for item in parsed.get("words", []):
                    box = item.get("box_2d", [0, 0, 0, 0])
                    if len(box) == 4:
                        ymin, xmin, ymax, xmax = box
                        words_list.append({
                            "text": item.get("text", ""),
                            "x": (xmin / 1000.0) * img_w,
                            "y": (ymin / 1000.0) * img_h,
                            "w": ((xmax - xmin) / 1000.0) * img_w,
                            "h": ((ymax - ymin) / 1000.0) * img_h,
                        })
                return {"words": words_list, "width": img_w, "height": img_h}
        except Exception as e:
            print(f"[OCR] Gemini box extraction error: {e}")
            continue

    return {"words": [], "width": img_w, "height": img_h}


async def extract_sandwich_boxes_parallel(
    image_bytes_list: List[bytes],
    credentials: Optional[Credentials] = None,
    gemini_api_key: Optional[str] = None,
) -> List[dict]:
    """
    Extracts word bounding boxes for Dual-Layer Sandwich PDF generation.
    Prefers Cloud Vision OCR for pixel-exact word coordinates; falls back to Gemini Flash.
    """
    if credentials:
        print(f"[OCR] Extracting word coordinates via Cloud Vision ({len(image_bytes_list)} images)...")
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, _ocr_single_vision_boxes, vision_client, img_bytes)
            for img_bytes in image_bytes_list
        ]
        results = await asyncio.gather(*tasks)
        return list(results)

    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        print(f"[OCR] Extracting word coordinates via Gemini Flash ({len(image_bytes_list)} images)...")
        async with httpx.AsyncClient() as client:
            tasks = [
                _extract_boxes_with_gemini(client, img_bytes, api_key)
                for img_bytes in image_bytes_list
            ]
            results = await asyncio.gather(*tasks)
            return list(results)

    raise RuntimeError("Neither GCP credentials nor GEMINI_API_KEY is configured for Sandwich PDF.")
