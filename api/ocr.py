import asyncio
import base64
import io
import json
import os
from typing import Any, List, Optional, Union

import httpx
from google.cloud import vision
from google.oauth2.service_account import Credentials
from PIL import Image

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
                        "enum": ["heading", "paragraph", "list", "quote", "table", "key_value"],
                    },
                    "text": {"type": "STRING"},
                    "level": {"type": "INTEGER"},
                    "align": {"type": "STRING", "enum": ["left", "center", "right"]},
                    "ordered": {"type": "BOOLEAN"},
                    "items": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
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
    "You are an expert document and book digitizer and professional typographer. Analyze this physical "
    "document or book image (Vietnamese & English). Extract the content in exact vertical reading order into "
    "clean, structured typographic blocks.\n"
    "- Typography & Inline Styling: Faithfully preserve inline formatting! Use **bold** for bold words/phrases, "
    "*italics* for book titles, Latin terms, quotes, or emphasized words. Do not strip inline formatting.\n"
    "- Document & Chapter Titles: Extract as 'heading' with level 1 (e.g. 'CHƯƠNG 1', book/article titles). Use level 2 for "
    "sections ('1.1 Khái niệm', 'Điều 1') and level 3 for subsections. Set align='center' for mottos/chapter titles, 'left' for sections.\n"
    "- Lists: Extract bulleted lists or numbered lists as 'list' blocks with 'items' (each item preserving inline **bold** or *italics*), "
    "and 'ordered'=true for numbered lists or false for bullet lists.\n"
    "- Inset Quotes / Callouts: Extract indented blockquotes or highlighted excerpt callouts as 'quote' blocks.\n"
    "- Tables: Extract tables into 'table' blocks with all header columns and row values. Do not miss table cells.\n"
    "- Labeled Form Fields: For key-value items (e.g. 'Họ và tên: ...', 'Địa chỉ: ...'), extract as 'key_value'.\n"
    "- Body Paragraphs: For all standard text, extract as 'paragraph'.\n"
    "CRITICAL FOR PROSE: In physical books, printed lines end arbitrarily at the page column margin. You MUST merge "
    "broken lines within the same sentence/paragraph into a single continuous, flowing block of text with proper spaces. "
    "Never keep accidental line breaks in the middle of sentences. Only create a new paragraph block where there is an "
    "actual paragraph break in the source text.\n"
    "Preserve exact Vietnamese diacritics, numbers, and spelling. Output strictly valid JSON matching the schema."
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


LAYOUT_ALIGNMENT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "page_margins": {
            "type": "OBJECT",
            "properties": {
                "left": {"type": "INTEGER", "description": "Left margin of body text column (0-1000)"},
                "right": {"type": "INTEGER", "description": "Right margin of body text column (0-1000)"},
                "top": {"type": "INTEGER", "description": "Top margin (0-1000)"},
                "bottom": {"type": "INTEGER", "description": "Bottom margin (0-1000)"},
            },
            "required": ["left", "right"],
        },
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {
                        "type": "STRING",
                        "description": "Proofread text with exact Vietnamese diacritics, spelling, punctuation, and casing",
                    },
                    "align": {
                        "type": "STRING",
                        "enum": ["left", "center", "right", "justify"],
                        "description": "Typographic alignment: 'center' for titles/mottos, 'right' for dates/signatures, 'left' for headings/lists, 'justify' for body paragraphs",
                    },
                    "type": {
                        "type": "STRING",
                        "enum": ["title", "heading", "body", "meta", "signature", "list_item", "table_row"],
                        "description": "Document semantic role",
                    },
                    "box_2d": {
                        "type": "ARRAY",
                        "items": {"type": "INTEGER"},
                        "description": "[ymin, xmin, ymax, xmax] normalized 0-1000 with leveled horizontal baseline (cancelling camera tilt)",
                    },
                    "font_size_pt": {
                        "type": "NUMBER",
                        "description": "Recommended font size in pt (e.g. 15 for titles, 12 for headings, 10 for body, 9 for signatures/meta)",
                    },
                    "is_bold": {"type": "BOOLEAN"},
                    "is_italic": {"type": "BOOLEAN"},
                    "is_paragraph_start": {
                        "type": "BOOLEAN",
                        "description": "True if this line begins a new body paragraph with first-line indent",
                    },
                },
                "required": ["text", "align", "box_2d"],
            },
        },
    },
    "required": ["lines"],
}


async def _align_and_proofread_layout_with_gemini(
    client: httpx.AsyncClient,
    image_bytes: bytes,
    page_data: Optional[dict],
    api_key: str,
) -> dict:
    """
    Multimodal Document Layout Engine & Typographer:
    Uses Gemini Flash with the original image (Layer 1) to:
    1. Inspect visual ink and proofread OCR typos & Vietnamese diacritics.
    2. Reconstruct typographic structure:
       - Centered document titles & chapter headings ('center')
       - Right-aligned dates, signature blocks, page numbers ('right')
       - Left-aligned/justified body paragraphs with first-line indents ('left' / 'justify')
    3. Level text baselines to completely eliminate camera tilt and perspective jitter.
    """
    img = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = img.size
    mime_type = _detect_mime_type(image_bytes)
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    draft_text = page_data.get("full_text", "") if page_data else ""

    prompt = (
        "You are an expert document typographer, layout reconstructor, and Vietnamese/English copyeditor.\n"
        "Your mission: Analyze this physical document photo (Layer 1) and reconstruct the physical document layout "
        "into clean, perfectly aligned typographic lines for a pristine digital white-canvas vector PDF.\n\n"
        "DOCUMENT SENSE & ALIGNMENT RULES:\n"
        "1. Centered Alignment ('align': 'center'):\n"
        "   - National motto (e.g. 'CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM', 'Độc lập - Tự do - Hạnh phúc').\n"
        "   - Document titles ('QUYẾT ĐỊNH', 'HỢP ĐỒNG', 'BÁO CÁO', 'GIẤY ĐỀ NGHỊ', 'CHƯƠNG I', 'BÀI 1').\n"
        "   - Centered subtitles, mottos, and main headings.\n"
        "   - Even if the camera capture is slightly tilted or off-center, recognize that these elements belong in the center.\n\n"
        "2. Right Alignment ('align': 'right'):\n"
        "   - Dates and locations (e.g., 'Hà Nội, ngày 24 tháng 10 năm 2026', '..., ngày... tháng... năm...').\n"
        "   - Signature titles and name blocks ('Người làm đơn', 'Giám đốc', '(Ký và ghi rõ họ tên)').\n"
        "   - Author attributions, epigraphs, header/footer page numbers.\n\n"
        "3. Left Alignment & Justification ('align': 'left' or 'justify'):\n"
        "   - Section headings ('1. Tổng quan', 'Điều 1.', 'Mục tiêu:').\n"
        "   - Standard body paragraphs (use 'justify' or 'left').\n"
        "   - Bulleted or numbered list items.\n"
        "   - Form field labels ('Họ và tên: ...', 'Địa chỉ: ...').\n\n"
        "4. Paragraph Indentation ('is_paragraph_start': true):\n"
        "   - Mark the first line of each new prose paragraph with is_paragraph_start: true.\n\n"
        "5. Camera Tilt & Skew Leveling:\n"
        "   - Phone photos often have tilted, sloping, or curved text baselines.\n"
        "   - Provide leveled, horizontal bounding boxes [ymin, xmin, ymax, xmax] (normalized 0-1000) so lines are rendered straight and horizontal.\n"
        "   - Define clean document page margins: left, right, top, bottom.\n\n"
        "6. Visual Proofreading & OCR Diacritics Correction:\n"
        "   - Read the visual ink on the page to fix any OCR mistakes (e.g. 'cl' -> 'd', 'rn' -> 'm', missing diacritics like 'o' -> 'ơ/ở/õ/ộ').\n"
        "   - Guarantee 100% correct Vietnamese diacritics, spelling, punctuation, and capitalization.\n"
        "   - Do NOT omit any visible text or words."
    )

    if draft_text.strip():
        prompt += f"\n\nRAW OCR RECOGNITION (DRAFT REFERENCE):\n{draft_text[:3500]}"

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
            "response_schema": LAYOUT_ALIGNMENT_SCHEMA,
            "temperature": 0.1,
        },
    }

    for model in GEMINI_MODELS:
        url = GEMINI_API_URL.format(model=model)
        try:
            resp = await client.post(url, params={"key": api_key}, json=payload, timeout=50.0)
            if resp.status_code == 200:
                data = resp.json()
                text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text_content)
                lines = parsed.get("lines", [])
                page_margins = parsed.get("page_margins", {"left": 80, "right": 920, "top": 60, "bottom": 940})

                # Synthesize words for invisible text mode (Sandwich PDF)
                synthetic_words = []
                for line in lines:
                    box = line.get("box_2d", [0, 0, 0, 0])
                    if len(box) != 4:
                        continue
                    ymin, xmin, ymax, xmax = box
                    line_text = line.get("text", "").strip()
                    if not line_text:
                        continue

                    line_w_px = max(1.0, ((xmax - xmin) / 1000.0) * img_w)
                    line_h_px = max(1.0, ((ymax - ymin) / 1000.0) * img_h)
                    start_x_px = (xmin / 1000.0) * img_w
                    start_y_px = (ymin / 1000.0) * img_h

                    tokens = line_text.split()
                    total_chars = max(1, sum(len(t) for t in tokens))
                    curr_x_px = start_x_px
                    space_w = (line_w_px * 0.12) / max(1, len(tokens) - 1) if len(tokens) > 1 else 0
                    avail_w = max(1.0, line_w_px - (space_w * (len(tokens) - 1)))

                    for t in tokens:
                        t_w = max(4.0, (len(t) / total_chars) * avail_w)
                        synthetic_words.append({
                            "text": t,
                            "x": curr_x_px,
                            "y": start_y_px,
                            "w": t_w,
                            "h": line_h_px,
                        })
                        curr_x_px += t_w + space_w

                words = page_data.get("words", []) if (page_data and page_data.get("words")) else synthetic_words

                print(f"[OCR] Gemini layout engine aligned {len(lines)} lines with document typography.")
                return {
                    "lines": lines,
                    "page_margins": page_margins,
                    "words": words,
                    "width": img_w,
                    "height": img_h,
                    "full_text": "\n".join(l.get("text", "") for l in lines),
                }
        except Exception as e:
            print(f"[OCR] Gemini layout alignment error with {model}: {e}")
            continue

    # Fallback to page_data if Gemini fails
    if page_data:
        return page_data
    return {"words": [], "lines": [], "width": img_w, "height": img_h, "full_text": ""}


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
    """Fallback: Extracts bounding boxes from Gemini Flash when layout alignment is not requested."""
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


PROOFREAD_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "corrections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "wrong": {"type": "STRING"},
                    "correct": {"type": "STRING"},
                },
                "required": ["wrong", "correct"],
            },
        }
    },
    "required": ["corrections"],
}


async def _proofread_words_with_gemini(
    client: httpx.AsyncClient,
    page_data: dict,
    api_key: str,
) -> dict:
    """Uses Gemini Flash to correct OCR misreads, broken tokens, and missing Vietnamese diacritics."""
    full_text = page_data.get("full_text", "")
    words = page_data.get("words", [])
    if not words or not full_text.strip():
        return page_data

    prompt = (
        "You are an expert Vietnamese and English copyeditor. Below is raw OCR-recognized text "
        "from a physical document photo that may contain OCR misreads, broken characters, or missing diacritics.\n"
        "Analyze the text and return a JSON list of word corrections: {'corrections': [{'wrong': '...', 'correct': '...'}]}.\n"
        "Rules: ONLY correct obvious OCR scanning errors (e.g., missing Vietnamese diacritics, broken words like 'cl' for 'd'). "
        "Do NOT paraphrase or rewrite. If a word is already correct, do not change it.\n\n"
        f"TEXT:\n{full_text[:4000]}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": PROOFREAD_SCHEMA,
            "temperature": 0.1,
        },
    }

    try:
        url = GEMINI_API_URL.format(model="gemini-2.0-flash")
        resp = await client.post(url, params={"key": api_key}, json=payload, timeout=20.0)
        if resp.status_code == 200:
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            corrections_list = json.loads(content).get("corrections", [])
            correction_map = {c["wrong"]: c["correct"] for c in corrections_list if c.get("wrong")}
            if correction_map:
                print(f"[OCR] Gemini NLP corrected {len(correction_map)} OCR words.")
                for w in words:
                    orig = w.get("text", "")
                    if orig in correction_map:
                        w["text"] = correction_map[orig]
    except Exception as e:
        print(f"[OCR] Gemini proofreading non-fatal error: {e}")

    return page_data


async def extract_sandwich_boxes_parallel(
    image_bytes_list: List[bytes],
    credentials: Optional[Credentials] = None,
    gemini_api_key: Optional[str] = None,
) -> List[dict]:
    """
    Extracts document layout, leveled lines, and word coordinates for:
    1. Spatial Pure White Layout PDF (uses Gemini-aligned lines + document typography sense).
    2. Dual-Layer Sandwich PDF (uses word boxes for invisible text overlay).

    When Gemini is available, uses multimodal visual understanding to:
    - Proofread text against original image ink.
    - Align document structure: centered titles, right-aligned dates/signatures, indented paragraphs.
    - Level baselines to cancel camera tilt and perspective jitter.
    """
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "").strip()

    if credentials:
        print(f"[OCR] Extracting word coordinates via Cloud Vision ({len(image_bytes_list)} images)...")
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, _ocr_single_vision_boxes, vision_client, img_bytes)
            for img_bytes in image_bytes_list
        ]
        vision_results = await asyncio.gather(*tasks)

        # Apply LLM Layout Aligner & Proofreader with Layer 1 visual context
        if api_key:
            print("[OCR] Running Gemini Flash layout aligner & proofreader with Layer 1 visual context...")
            async with httpx.AsyncClient() as client:
                align_tasks = [
                    _align_and_proofread_layout_with_gemini(client, img_bytes, page, api_key)
                    for img_bytes, page in zip(image_bytes_list, vision_results)
                ]
                enhanced_results = await asyncio.gather(*align_tasks)
                return list(enhanced_results)

        return list(vision_results)

    if api_key:
        print(f"[OCR] Extracting document layout & words via Gemini Flash ({len(image_bytes_list)} images)...")
        async with httpx.AsyncClient() as client:
            tasks = [
                _align_and_proofread_layout_with_gemini(client, img_bytes, None, api_key)
                for img_bytes in image_bytes_list
            ]
            results = await asyncio.gather(*tasks)
            return list(results)

    raise RuntimeError("Neither GCP credentials nor GEMINI_API_KEY is configured for document layout extraction.")
