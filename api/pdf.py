import os
from pathlib import Path
from typing import List, Optional

from fpdf import FPDF


FONT_DIR = Path(__file__).parent / "fonts"
REGULAR_FONT = str(FONT_DIR / "Roboto-Regular.ttf")
BOLD_FONT = str(FONT_DIR / "Roboto-Bold.ttf")


class CleanDocumentPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        if "Roboto" in self.fonts:
            self.set_font("Roboto", "", 9)
        else:
            self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Trang {self.page_no()} / {{nb}}", align="C")


def _render_text_cell(
    pdf: FPDF,
    text: str,
    font_size: float = 10.5,
    is_bold: bool = False,
    align: str = "L",
    line_height: float = 5.5,
    has_roboto: bool = True,
):
    """
    Renders text safely without running out of horizontal space.
    - Always resets X to left margin.
    - Uses exact effective page width (pdf.epw).
    - Enables character-level wrapping (wrapmode="CHAR") for long tokens.
    - Auto-scales font down if text is unusually dense or wide.
    """
    if not text:
        return

    # Ensure cursor is at left margin
    pdf.set_x(pdf.l_margin)
    width = pdf.epw

    if has_roboto:
        font_family = "Roboto"
        style = "B" if is_bold else ""
    else:
        font_family = "Helvetica"
        style = "B" if is_bold else ""
        text = text.encode("latin-1", "replace").decode("latin-1")

    # Try rendering with desired font size; auto-scale down if tight
    for size_attempt in [font_size, max(7.0, font_size - 2), 6.0]:
        pdf.set_font(font_family, style, size_attempt)
        pdf.set_x(pdf.l_margin)
        try:
            pdf.multi_cell(
                w=width,
                h=line_height,
                text=text,
                align=align,
                wrapmode="CHAR",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            return
        except Exception:
            pdf.set_x(pdf.l_margin)
            continue

    # Final emergency fallback: truncate line if extreme
    try:
        pdf.set_font(font_family, style, 6.0)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(
            w=width,
            h=line_height,
            text=text[:150],
            align="L",
            wrapmode="CHAR",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    except Exception as e:
        print(f"[PDF] Could not render line: {e}")


def build_pdf(
    image_bytes_list: List[bytes],
    ocr_texts: List[Optional[str]],
) -> bytes:
    """
    Builds a clean, freshly digitized PDF document from OCR text.

    - Pure white A4 background (no camera photos, shadows, or artifacts).
    - TrueType Unicode typography (Roboto) supporting Vietnamese & English.
    - Safe auto-scaled text rendering (no horizontal space errors).
    - Professional document margins, paragraph spacing, and centered headings.
    - Automatic page numbering.

    Returns the generated PDF as raw bytes.
    """
    pdf = CleanDocumentPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)

    # Register Unicode TTF fonts for Vietnamese + English
    has_roboto = False
    if os.path.exists(REGULAR_FONT):
        try:
            pdf.add_font("Roboto", "", REGULAR_FONT)
            if os.path.exists(BOLD_FONT):
                pdf.add_font("Roboto", "B", BOLD_FONT)
            has_roboto = True
            print("[PDF] Loaded Roboto TrueType fonts successfully.")
        except Exception as e:
            print(f"[PDF] Failed to load Roboto: {e}")

    for idx, text in enumerate(ocr_texts):
        pdf.add_page()
        pdf.set_x(pdf.l_margin)

        if not text or not text.strip():
            _render_text_cell(
                pdf=pdf,
                text="[Không nhận diện được nội dung văn bản]",
                font_size=10.0,
                align="C",
                has_roboto=has_roboto,
            )
            continue

        lines = text.split("\n")
        for line in lines:
            line_str = line.strip()
            if not line_str:
                pdf.ln(2.5)
                pdf.set_x(pdf.l_margin)
                continue

            upper_line = line_str.upper()
            is_national_motto = (
                "CỘNG HÒA" in upper_line
                or "ĐỘC LẬP" in upper_line
                or "SOCIALIST REPUBLIC" in upper_line
                or "INDEPENDENCE" in upper_line
            )
            is_contract_title = (
                "HỢP ĐỒNG" in upper_line
                or "LABOR CONTRACT" in upper_line
                or "EMPLOYMENT CONTRACT" in upper_line
                or "THỎA THUẬN" in upper_line
                or "BIÊN BẢN" in upper_line
                or "QUYẾT ĐỊNH" in upper_line
            )
            is_article_heading = (
                line_str.startswith("Điều ")
                or line_str.startswith("ĐIỀU ")
                or line_str.startswith("Article ")
                or line_str.startswith("ARTICLE ")
                or line_str.startswith("Chương ")
                or line_str.startswith("CHƯƠNG ")
            )

            if is_contract_title:
                pdf.set_text_color(15, 15, 15)
                _render_text_cell(
                    pdf=pdf,
                    text=line_str,
                    font_size=13.0,
                    is_bold=True,
                    align="C",
                    line_height=6.5,
                    has_roboto=has_roboto,
                )
                pdf.ln(1.5)
            elif is_national_motto:
                pdf.set_text_color(25, 25, 25)
                _render_text_cell(
                    pdf=pdf,
                    text=line_str,
                    font_size=11.0,
                    is_bold=True,
                    align="C",
                    line_height=5.8,
                    has_roboto=has_roboto,
                )
            elif is_article_heading:
                pdf.ln(1.5)
                pdf.set_text_color(20, 20, 20)
                _render_text_cell(
                    pdf=pdf,
                    text=line_str,
                    font_size=11.0,
                    is_bold=True,
                    align="L",
                    line_height=5.8,
                    has_roboto=has_roboto,
                )
            else:
                pdf.set_text_color(35, 35, 35)
                _render_text_cell(
                    pdf=pdf,
                    text=line_str,
                    font_size=10.0,
                    is_bold=False,
                    align="L",
                    line_height=5.2,
                    has_roboto=has_roboto,
                )

    return bytes(pdf.output())
