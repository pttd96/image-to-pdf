import io
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fpdf import FPDF
from fpdf.enums import TextMode
from PIL import Image, ImageChops, ImageEnhance, ImageFilter

FONT_DIR = Path(__file__).parent / "fonts"
REGULAR_FONT = str(FONT_DIR / "Roboto-Regular.ttf")


def whiten_document_image(img: Image.Image, enhance_contrast: float = 1.35) -> Image.Image:
    """
    Applies per-channel Illumination Division to eliminate camera shadows and paper discoloration.
    Turns paper backgrounds into crisp flatbed #FFFFFF while preserving original book typography
    and colored ink (e.g. red company stamps or blue signatures).
    """
    rgb = img.convert("RGB")
    r, g, b = rgb.split()

    w, h = img.size
    blur_radius = max(20, int(min(w, h) / 70))

    r_bg = r.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    g_bg = g.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    b_bg = b.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    r_div = ImageChops.divide(r, r_bg)
    g_div = ImageChops.divide(g, g_bg)
    b_div = ImageChops.divide(b, b_bg)

    whitened_rgb = Image.merge("RGB", (r_div, g_div, b_div))

    if enhance_contrast > 1.0:
        enhancer = ImageEnhance.Contrast(whitened_rgb)
        whitened_rgb = enhancer.enhance(enhance_contrast)

    return whitened_rgb


def build_sandwich_pdf(
    image_bytes_list: List[bytes],
    pages_data: List[Dict[str, Any]],
    whiten_bg: bool = True,
) -> bytes:
    """
    Builds a Dual-Layer Sandwich PDF (Adobe Scan / Apple Notes style):
    - Layer 1: High-resolution original photo covering 100% of the page (100% visual fidelity).
    - Layer 2: Invisible searchable text (TextMode.INVISIBLE = 3 Tr) overlaid at exact word coordinates.

    Preserves 100% authentic typography, book margins, italics, tables, and stamps while making
    the document fully searchable (Cmd+F / Ctrl+F) and copy-pasteable.
    """
    pdf = FPDF(unit="pt")

    has_roboto = False
    if os.path.exists(REGULAR_FONT):
        try:
            pdf.add_font("Roboto", "", REGULAR_FONT)
            has_roboto = True
        except Exception as e:
            print(f"[SandwichPDF] Failed to load Roboto font: {e}")

    for idx, img_bytes in enumerate(image_bytes_list):
        img = Image.open(io.BytesIO(img_bytes))
        img_w, img_h = img.size

        # Standardize page width to standard A4 (595.28 pt) while maintaining aspect ratio
        target_w = 595.28
        scale = target_w / float(img_w)
        target_h = float(img_h) * scale

        pdf.add_page(format=(target_w, target_h))
        pdf.set_margins(left=0, top=0, right=0)
        pdf.set_auto_page_break(False)

        # Layer 1: Process and draw background
        processed_img = whiten_document_image(img) if whiten_bg else img
        ext = ".png"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            processed_img.save(tmp, format="PNG")
            tmp_path = tmp.name

        try:
            pdf.image(tmp_path, x=0, y=0, w=target_w, h=target_h)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # Layer 2: Overlay invisible searchable text at exact coordinates
        page_info = pages_data[idx] if idx < len(pages_data) else {}
        words = page_info.get("words", [])

        if not words:
            continue

        pdf.text_mode = TextMode.INVISIBLE
        if has_roboto:
            pdf.set_font("Roboto", "", 10)
        else:
            pdf.set_font("Helvetica", "", 10)

        src_w = page_info.get("width") or img_w
        src_h = page_info.get("height") or img_h

        coord_scale_x = target_w / float(src_w)
        coord_scale_y = target_h / float(src_h)

        for w in words:
            word_text = str(w.get("text", "")).strip()
            if not word_text:
                continue

            scaled_x = float(w["x"]) * coord_scale_x
            scaled_y = float(w["y"]) * coord_scale_y
            scaled_h = float(w.get("h", 12)) * coord_scale_y

            # In PDF text(), (x, y) is the font baseline
            baseline_y = scaled_y + (scaled_h * 0.85)
            font_size = max(4.0, min(72.0, scaled_h * 0.85))

            pdf.set_font_size(font_size)
            try:
                pdf.text(x=scaled_x, y=baseline_y, text=word_text)
            except Exception:
                # Fallback for characters unsupported by standard font
                pass

    return bytes(pdf.output())
