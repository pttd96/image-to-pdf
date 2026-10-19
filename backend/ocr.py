import asyncio
from typing import List, Optional
from google.cloud import vision


def _ocr_single(client: vision.ImageAnnotatorClient, image_bytes: bytes) -> Optional[str]:
    """Runs DOCUMENT_TEXT_DETECTION on a single image. Returns extracted text or None."""
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")
    annotation = response.full_text_annotation
    return annotation.text if annotation else None


async def run_ocr_parallel(image_bytes_list: List[bytes]) -> List[Optional[str]]:
    """
    Runs OCR on all images concurrently using asyncio.
    Returns a list of extracted text strings (one per image, None if no text found).
    """
    client = vision.ImageAnnotatorClient()

    loop = asyncio.get_event_loop()

    tasks = [
        loop.run_in_executor(None, _ocr_single, client, img_bytes)
        for img_bytes in image_bytes_list
    ]
    results = await asyncio.gather(*tasks)
    return list(results)
