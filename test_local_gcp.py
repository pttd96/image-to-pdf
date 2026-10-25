#!/usr/bin/env python3
"""
Test script to verify local connection to GCP (Google Cloud Vision API & Gemini API).

Usage:
    python test_local_gcp.py
"""

import io
import os
import sys
import time
from pathlib import Path

# Ensure api directory is in Python path
sys.path.insert(0, str(Path(__file__).parent / "api"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import httpx
from PIL import Image, ImageDraw


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def test_gemini_api():
    print_header("1. Testing Gemini Flash API Connection")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("❌ [FAIL] GEMINI_API_KEY is not set in .env")
        return False

    masked_key = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
    print(f"🔑 Found GEMINI_API_KEY: {masked_key}")

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    payload = {
        "contents": [{"parts": [{"text": "Reply with only the word 'OK' if you can read this."}]}]
    }

    try:
        t0 = time.time()
        resp = httpx.post(url, params={"key": api_key}, json=payload, timeout=15.0)
        elapsed = int((time.time() - t0) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            print(f"✅ [PASS] Gemini API responded in {elapsed}ms! Response: '{reply}'")
            return True
        else:
            print(f"❌ [FAIL] Gemini API returned HTTP {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"❌ [FAIL] Gemini API request failed: {e}")
        return False


def test_cloud_vision_api():
    print_header("2. Testing Google Cloud Vision API Connection")

    # Step A: Check credentials
    credentials = None
    auth_method = "None"

    # 1. Check raw JSON in .env
    raw_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if raw_json:
        try:
            import json
            from google.oauth2 import service_account
            info = json.loads(raw_json)
            credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            auth_method = f"GOOGLE_CREDENTIALS_JSON (.env, SA: {info.get('client_email', 'unknown')})"
        except Exception as e:
            print(f"⚠️ Failed to parse GOOGLE_CREDENTIALS_JSON: {e}")

    # 2. Check file path in GOOGLE_APPLICATION_CREDENTIALS
    if not credentials:
        cred_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if cred_file and os.path.isfile(cred_file):
            try:
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    cred_file,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                auth_method = f"GOOGLE_APPLICATION_CREDENTIALS file ({cred_file})"
            except Exception as e:
                print(f"⚠️ Failed to read credential file {cred_file}: {e}")

    # 3. Check gcloud Application Default Credentials (ADC)
    if not credentials:
        try:
            import google.auth
            credentials, project_id = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_method = f"gcloud Application Default Credentials (ADC, Project: {project_id or 'default'})"
        except Exception:
            pass

    if not credentials:
        print("❌ [FAIL] No GCP credentials detected on your local machine.")
        print("\n👉 How to connect locally (pick ONE):")
        print("   Option 1 (Recommended - Fastest):")
        print("      Run in terminal: gcloud auth application-default login")
        print("   Option 2:")
        print("      Create a Service Account key in GCP Console, save as 'service-account.json',")
        print("      and set in .env: GOOGLE_APPLICATION_CREDENTIALS='./service-account.json'")
        print("   Option 3:")
        print("      Paste the full JSON content into .env: GOOGLE_CREDENTIALS_JSON='{...}'")
        return False

    print(f"🔐 Authenticated via: {auth_method}")

    # Step B: Create a synthetic test image with text
    img = Image.new("RGB", (320, 80), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 25), "GOOGLE CLOUD VISION OCR OK", fill=(0, 0, 0))

    img_bytes_io = io.BytesIO()
    img.save(img_bytes_io, format="PNG")
    test_image_bytes = img_bytes_io.getvalue()

    # Step C: Call Vision API
    try:
        from google.cloud import vision
        client = vision.ImageAnnotatorClient(credentials=credentials)
        image = vision.Image(content=test_image_bytes)

        t0 = time.time()
        response = client.document_text_detection(image=image)
        elapsed = int((time.time() - t0) * 1000)

        if response.error.message:
            print(f"❌ [FAIL] Cloud Vision API returned an error: {response.error.message}")
            if "has not been used in project" in response.error.message or "disabled" in response.error.message:
                print("👉 Fix: Enable the Vision API in your project by running:")
                print("   gcloud services enable vision.googleapis.com")
            return False

        detected_text = response.full_text_annotation.text.strip() if response.full_text_annotation else ""
        print(f"✅ [PASS] Cloud Vision API responded in {elapsed}ms!")
        print(f"   Detected Text: '{detected_text}'")
        return True

    except Exception as e:
        print(f"❌ [FAIL] Cloud Vision API call failed: {e}")
        err_str = str(e)
        if "API has not been used" in err_str or "it is disabled" in err_str:
            print("\n👉 Fix: Enable the Vision API in your project:")
            print("   gcloud services enable vision.googleapis.com")
        elif "PermissionDenied" in err_str or "403" in err_str:
            print("\n👉 Fix: Ensure your GCP account or service account has the role 'Cloud Vision API User' or 'Editor'.")
        return False


def main():
    print("\n🔍 Checking Local Connection to Google Cloud Platform...")

    gemini_ok = test_gemini_api()
    vision_ok = test_cloud_vision_api()

    print_header("SUMMARY")
    print(f"• Gemini Flash API:     {'✅ READY' if gemini_ok else '❌ NOT READY'}")
    print(f"• Cloud Vision API:     {'✅ READY' if vision_ok else '❌ NOT READY'}")

    if gemini_ok or vision_ok:
        print("\n🚀 Ready to run the local server:")
        print("   uvicorn api.index:app --reload --port 8000")
        print("   Then open: http://localhost:8000\n")
    else:
        print("\n⚠️ Please configure either GEMINI_API_KEY or GCP credentials before starting the server.\n")


if __name__ == "__main__":
    main()
