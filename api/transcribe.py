import asyncio
import base64
import os
from typing import Optional

import httpx

GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-2.5-flash"]
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

WORD_TYPIST_SYSTEM_INSTRUCTION = (
    "You are an expert document typist and digitizer. Your task is to transcribe this physical document image "
    "(Vietnamese & English) into clean, perfectly structured Markdown as if you were typing it into Microsoft Word.\n\n"
    "Follow these strict formatting rules:\n"
    "1. HEADINGS & MOTTOS:\n"
    "   - For national mottos and document titles (e.g. 'CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM', 'Độc lập - Tự do - Hạnh phúc', "
    "     'HỢP ĐỒNG KINH TẾ', book titles), center them using: <center># TITLE</center> or <center>**TITLE**</center>.\n"
    "   - For articles and sections (e.g. 'Điều 1: Đối tượng hợp đồng', 'Chương I: Quy định chung', '1.1 Khái niệm'), "
    "     format with '## ' or '### '.\n\n"
    "2. PARAGRAPHS & BODY TEXT:\n"
    "   - Physical lines on a page are wrapped due to paper width. You MUST unwrap and merge continuous lines belonging to the "
    "     same sentence or paragraph into a single flowing block. Do NOT introduce line breaks in the middle of sentences.\n"
    "   - Separate distinct paragraphs with a blank line.\n\n"
    "3. TABLES & FORM GRIDS:\n"
    "   - Convert all tables, salary breakdowns, compensation lists, and financial grids into standard GitHub Flavored Markdown tables:\n"
    "     | STT | Nội dung | Đơn vị | Đơn giá | Thành tiền |\n"
    "     |---|---|---|---|---|\n"
    "     | 01 | Dịch vụ A | Tháng | 1.000.000 | 1.000.000 |\n"
    "   - In Labor Contracts ('HỢP ĐỒNG LAO ĐỘNG'), ensure all structured tables such as salary/allowance matrices, "
    "     working hours, probationary terms, or side-by-side party information are extracted as explicit Markdown tables.\n"
    "   - Never omit columns or drop rows. Preserve exact figures.\n\n"
    "4. SIGNATURE BLOCKS:\n"
    "   - For legal contracts and official forms with side-by-side signature areas at the bottom, format them as a 2-column markdown table:\n"
    "     | ĐẠI DIỆN BÊN A | ĐẠI DIỆN BÊN B |\n"
    "     | :--- | :--- |\n"
    "     | (Ký, đóng dấu, ghi rõ họ tên) | (Ký, đóng dấu, ghi rõ họ tên) |\n\n"
    "5. ACCURACY:\n"
    "   - Preserve exact Vietnamese spelling, diacritical marks (dấu hỏi, ngã, nặng, sắc, huyền), dates, and numbers.\n"
    "   - Return ONLY the clean Markdown text. Do not wrap in extra markdown ```markdown code fences."
)

DIFF_AUDITOR_SYSTEM_INSTRUCTION = (
    "You are a rigorous legal document auditor. Compare the provided document image against the draft Markdown transcription.\n"
    "Your objective: detect any dropped table rows, missing numbers/amounts, omitted signature names, or OCR typos against the ink.\n"
    "Strict rule: Fix ONLY inaccuracies against the image. Do NOT alter the layout or reformat. Return the corrected Markdown text directly."
)


def _detect_mime_type(image_bytes: bytes) -> str:
    """Detects image MIME type from magic bytes."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def transcribe_image_to_markdown(
    client: httpx.AsyncClient,
    image_bytes: bytes,
    api_key: str,
    enable_diff_audit: bool = False,
) -> str:
    """
    Transcribes a document image into clean Microsoft Word style Markdown
    using Gemini 2.0 Flash (with 1.5 Flash fallback).
    """
    mime_type = _detect_mime_type(image_bytes)
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": WORD_TYPIST_SYSTEM_INSTRUCTION},
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
            "temperature": 0.1,
            "maxOutputTokens": 8192,
        },
    }

    markdown_result = ""
    for model_name in GEMINI_MODELS:
        endpoint = GEMINI_API_URL.format(model=model_name)
        try:
            resp = await client.post(
                endpoint,
                params={"key": api_key},
                json=payload,
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        markdown_result = parts[0].get("text", "").strip()
                        if markdown_result:
                            # Strip wrapping ```markdown code blocks if LLM included them
                            if markdown_result.startswith("```markdown"):
                                markdown_result = markdown_result[len("```markdown") :].strip()
                            if markdown_result.startswith("```"):
                                markdown_result = markdown_result[3:].strip()
                            if markdown_result.endswith("```"):
                                markdown_result = markdown_result[:-3].strip()
                            break
            elif resp.status_code == 402:
                print(
                    f"\n🚨 [BILLING ALERT] Gemini API credits depleted (HTTP 402 Payment Required)!\n"
                    f"   Model: {model_name}\n"
                    f"   Details: {resp.text[:200]}\n"
                    f"   👉 Table and Word-style layout extraction is disabled until credits are recharged.\n"
                    f"   👉 Manage billing / API keys at: https://aistudio.google.com\n"
                )
                # No need to retry other models; the billing account is depleted
                break
            elif resp.status_code in (401, 403):
                print(
                    f"\n🚨 [AUTH ERROR] Gemini API key unauthorized or invalid (HTTP {resp.status_code})!\n"
                    f"   Details: {resp.text[:200]}\n"
                )
                break
            else:
                print(f"[TRANSCRIBE] {model_name} HTTP {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            print(f"[TRANSCRIBE] Error calling {model_name}: {e}")

    if not markdown_result:
        return ""

    if not enable_diff_audit:
        return markdown_result

    # Optional diff audit pass for high-stakes documents
    try:
        audit_payload = {
            "contents": [
                {
                    "parts": [
                        {"text": DIFF_AUDITOR_SYSTEM_INSTRUCTION},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_data,
                            }
                        },
                        {
                            "text": f"Here is the draft Markdown transcription to verify:\n\n{markdown_result}",
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 8192,
            },
        }
        audit_resp = await client.post(
            GEMINI_API_URL.format(model="gemini-3.8-flash"),
            params={"key": api_key},
            json=audit_payload,
            timeout=25.0,
        )
        if audit_resp.status_code == 200:
            audit_data = audit_resp.json()
            candidates = audit_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    audited = parts[0].get("text", "").strip()
                    if audited:
                        if audited.startswith("```markdown"):
                            audited = audited[len("```markdown") :].strip()
                        if audited.startswith("```"):
                            audited = audited[3:].strip()
                        if audited.endswith("```"):
                            audited = audited[:-3].strip()
                        return audited
    except Exception as e:
        print(f"[TRANSCRIBE] Audit pass skipped: {e}")

    return markdown_result
