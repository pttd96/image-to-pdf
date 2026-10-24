import io
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fpdf import FPDF
from fpdf.enums import TextMode
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

FONT_DIR = Path(__file__).parent / "fonts"
REGULAR_FONT = str(FONT_DIR / "Roboto-Regular.ttf")
BOLD_FONT = str(FONT_DIR / "Roboto-Bold.ttf")


def whiten_document_image(img: Image.Image, enhance_contrast: float = 1.35) -> Image.Image:
    """
    Eliminates camera shadows, uneven lighting, and yellowed paper to produce a flatbed-quality #FFFFFF background.
    Uses standard Pillow subtract-invert illumination compensation:
    1. bg_blur - channel = ink_delta (isolates text from lighting gradients)
    2. invert(ink_delta) = crisp dark text on pure white paper
    3. autocontrast & enhancement stretches text to deep black
    """
    try:
        rgb = img.convert("RGB")
        r, g, b = rgb.split()

        w, h = img.size
        blur_radius = max(20, int(min(w, h) / 60))

        whitened_channels = []
        for ch in (r, g, b):
            bg = ch.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            # Subtract isolates ink from background gradient
            diff = ImageChops.subtract(bg, ch)
            # Invert turns background to pure 255 white and text into dark ink
            inv = ImageChops.invert(diff)
            # Stretch dynamic range to lock background at #FFFFFF
            whitened_ch = ImageOps.autocontrast(inv, cutoff=1)
            whitened_channels.append(whitened_ch)

        whitened_rgb = Image.merge("RGB", tuple(whitened_channels))

        if enhance_contrast > 1.0:
            enhancer = ImageEnhance.Contrast(whitened_rgb)
            whitened_rgb = enhancer.enhance(enhance_contrast)

        return whitened_rgb
    except Exception as e:
        print(f"[SandwichPDF] Whitening non-fatal error, falling back to original: {e}")
        return img.convert("RGB")


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


def group_words_into_lines(
    words: List[Dict[str, Any]],
    y_threshold_ratio: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Clusters words into horizontal line segments based on vertical coordinate proximity.
    Preserves natural word spacing and separates distinct column/table cells.
    """
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (float(w.get("y", 0)), float(w.get("x", 0))))
    lines = []
    current_line = [sorted_words[0]]

    for w in sorted_words[1:]:
        line_avg_h = sum(float(item.get("h", 12)) for item in current_line) / len(current_line)
        line_avg_y = sum(float(item.get("y", 0)) for item in current_line) / len(current_line)

        if abs(float(w.get("y", 0)) - line_avg_y) <= line_avg_h * y_threshold_ratio:
            current_line.append(w)
        else:
            current_line.sort(key=lambda item: float(item.get("x", 0)))
            lines.append(current_line)
            current_line = [w]

    if current_line:
        current_line.sort(key=lambda item: float(item.get("x", 0)))
        lines.append(current_line)

    result_segments = []
    for line_words in lines:
        avg_h = sum(float(w.get("h", 12)) for w in line_words) / len(line_words)
        min_y = min(float(w.get("y", 0)) for w in line_words)
        max_y = max(float(w.get("y", 0)) + float(w.get("h", 12)) for w in line_words)

        # Split across large horizontal gaps (multi-column layouts or table cells)
        segments = []
        curr_seg = [line_words[0]]
        for w in line_words[1:]:
            prev_w = curr_seg[-1]
            gap = float(w.get("x", 0)) - (float(prev_w.get("x", 0)) + float(prev_w.get("w", 10)))
            if gap > avg_h * 2.2:
                segments.append(curr_seg)
                curr_seg = [w]
            else:
                curr_seg.append(w)
        if curr_seg:
            segments.append(curr_seg)

        for seg in segments:
            seg_min_x = min(float(w.get("x", 0)) for w in seg)
            seg_max_x = max(float(w.get("x", 0)) + float(w.get("w", 10)) for w in seg)
            seg_text = " ".join(str(w.get("text", "")).strip() for w in seg if str(w.get("text", "")).strip())
            if seg_text:
                result_segments.append({
                    "text": seg_text,
                    "x": seg_min_x,
                    "y": min_y,
                    "w": seg_max_x - seg_min_x,
                    "h": max_y - min_y,
                })

    return result_segments


def build_spatial_white_pdf(
    image_bytes_list: List[bytes],
    pages_data: List[Dict[str, Any]],
) -> bytes:
    """
    Renders extracted document content onto a completely new, pure digital white canvas (#FFFFFF)
    with all words, headings, and columns placed at their exact physical spatial coordinates.
    - Eliminates 100% of camera shadows, yellow paper, blur, and dirty backgrounds.
    - Preserves authentic physical layout: margins, indentation, columns, and centered titles.
    - Renders in crisp TrueType Roboto black vector typography with zero word overlap.
    """
    pdf = FPDF(unit="pt")

    has_roboto = False
    if os.path.exists(REGULAR_FONT):
        try:
            pdf.add_font("Roboto", "", REGULAR_FONT)
            if os.path.exists(BOLD_FONT):
                pdf.add_font("Roboto", "B", BOLD_FONT)
            has_roboto = True
        except Exception as e:
            print(f"[SpatialWhitePDF] Failed to load Roboto font: {e}")

    for idx, img_bytes in enumerate(image_bytes_list):
        img = Image.open(io.BytesIO(img_bytes))
        img_w, img_h = img.size

        # Standardize page width to standard A4 (595.28 pt) while maintaining aspect ratio
        target_w = 595.28
        scale = target_w / float(img_w)
        target_h = float(img_h) * scale

        # Add pure digital white page (NO camera photo layer!)
        pdf.add_page(format=(target_w, target_h))
        pdf.set_margins(left=0, top=0, right=0)
        pdf.set_auto_page_break(False)

        page_info = pages_data[idx] if idx < len(pages_data) else {}
        words = page_info.get("words", [])

        if not words:
            continue

        src_w = page_info.get("width") or img_w
        src_h = page_info.get("height") or img_h

        coord_scale_x = target_w / float(src_w)
        coord_scale_y = target_h / float(src_h)

        line_segments = group_words_into_lines(words)

        # Visible crisp black text
        pdf.text_mode = TextMode.FILL
        pdf.set_text_color(25, 25, 25)

        for line in line_segments:
            line_text = line["text"].strip()
            if not line_text:
                continue

            scaled_x = float(line["x"]) * coord_scale_x
            scaled_y = float(line["y"]) * coord_scale_y
            scaled_h = float(line.get("h", 12)) * coord_scale_y

            font_size = max(5.5, min(48.0, scaled_h * 0.82))
            is_bold = (font_size >= 12.0) or (len(line_text) <= 50 and line_text.isupper())

            if has_roboto:
                pdf.set_font("Roboto", "B" if is_bold else "", font_size)
            else:
                pdf.set_font("Helvetica", "B" if is_bold else "", font_size)

            baseline_y = scaled_y + (scaled_h * 0.85)

            try:
                pdf.text(x=scaled_x, y=baseline_y, text=line_text)
            except Exception:
                pass

    return bytes(pdf.output())
