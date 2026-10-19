import io
from typing import List, Optional

from fpdf import FPDF
from PIL import Image


# A4 dimensions in mm
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297


def _image_bytes_to_temp_png(image_bytes: bytes) -> tuple[bytes, int, int]:
    """
    Converts raw image bytes to PNG (ensuring compatibility with fpdf2)
    and returns (png_bytes, width_px, height_px).
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        width, height = img.size
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), width, height


def build_pdf(
    image_bytes_list: List[bytes],
    ocr_texts: List[Optional[str]],
) -> bytes:
    """
    Builds a multi-page searchable PDF.

    Each page contains:
    - The original image rendered at full A4 width
    - An invisible text layer with the OCR-extracted text (for searchability)

    Returns the PDF as raw bytes.
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    for image_bytes, text in zip(image_bytes_list, ocr_texts):
        png_bytes, img_w, img_h = _image_bytes_to_temp_png(image_bytes)

        # Calculate height to maintain aspect ratio within A4
        aspect = img_h / img_w
        rendered_h = min(A4_WIDTH_MM * aspect, A4_HEIGHT_MM)

        pdf.add_page()

        # --- Image layer ---
        pdf.image(
            io.BytesIO(png_bytes),
            x=0,
            y=0,
            w=A4_WIDTH_MM,
            h=rendered_h,
        )

        # --- Invisible text layer (searchable) ---
        if text:
            try:
                pdf.set_xy(0, 0)
                pdf.set_font("Helvetica", size=1)   # Tiny font — visually invisible
                pdf.set_text_color(255, 255, 255)    # White text on white
                pdf.set_fill_color(255, 255, 255)
                # Ensure text is safely encodable for core latin-1 font
                safe_text = text.encode("latin-1", "replace").decode("latin-1")
                pdf.multi_cell(
                    w=A4_WIDTH_MM,
                    h=2,
                    text=safe_text,
                    border=0,
                    align="L",
                )
            except Exception:
                pass

    return bytes(pdf.output())
