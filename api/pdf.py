import os
from pathlib import Path
from typing import List, Optional

from fpdf import FPDF


FONT_DIR = Path(__file__).parent / "fonts"
REGULAR_FONT = str(FONT_DIR / "Roboto-Regular.ttf")
BOLD_FONT = str(FONT_DIR / "Roboto-Bold.ttf")


class CleanDocumentPDF(FPDF):
    def footer(self):
        # Position 15 mm from bottom
        self.set_y(-15)
        if "Roboto" in self.fonts:
            self.set_font("Roboto", "", 9)
        else:
            self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Trang {self.page_no()} / {{nb}}", align="C")


def build_pdf(
    image_bytes_list: List[bytes],
    ocr_texts: List[Optional[str]],
) -> bytes:
    """
    Builds a clean, freshly digitized PDF document from OCR text.

    - Pure white A4 background (no camera photos, shadows, or artifacts).
    - TrueType Unicode typography (Roboto) supporting Vietnamese & English.
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

        if not text or not text.strip():
            # If no text detected for this page
            if has_roboto:
                pdf.set_font("Roboto", "", 10)
                pdf.set_text_color(160, 160, 160)
            else:
                pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 10, "[Không nhận diện được nội dung văn bản]", align="C")
            continue

        lines = text.split("\n")
        for line in lines:
            line_str = line.strip()
            if not line_str:
                pdf.ln(3)  # Paragraph gap
                continue

            # Detect common contract headers, national emblems, titles, and articles
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

            is_header = is_national_motto or is_contract_title or is_article_heading

            if has_roboto:
                if is_contract_title:
                    pdf.set_font("Roboto", "B", 14)
                    pdf.set_text_color(15, 15, 15)
                    pdf.multi_cell(0, 7, line_str, align="C")
                    pdf.ln(2)
                elif is_national_motto:
                    pdf.set_font("Roboto", "B", 11)
                    pdf.set_text_color(25, 25, 25)
                    pdf.multi_cell(0, 6, line_str, align="C")
                    pdf.ln(1)
                elif is_article_heading:
                    pdf.ln(2)
                    pdf.set_font("Roboto", "B", 11.5)
                    pdf.set_text_color(20, 20, 20)
                    pdf.multi_cell(0, 6, line_str, align="L")
                    pdf.ln(1)
                elif is_header:
                    pdf.set_font("Roboto", "B", 11)
                    pdf.set_text_color(20, 20, 20)
                    pdf.multi_cell(0, 5.8, line_str, align="L")
                else:
                    pdf.set_font("Roboto", "", 10.5)
                    pdf.set_text_color(35, 35, 35)
                    # Justify longer paragraph lines, left-align short lines
                    align = "J" if len(line_str) > 60 else "L"
                    pdf.multi_cell(0, 5.6, line_str, align=align)
            else:
                # Fallback to standard core font if TTF missing
                pdf.set_font("Helvetica", "B" if is_header else "", 10)
                safe = line_str.encode("latin-1", "replace").decode("latin-1")
                pdf.multi_cell(0, 5.5, safe)

    return bytes(pdf.output())
