import math
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from fpdf import FPDF

try:
    from models import HeadingBlock, KeyValueBlock, PageDocument, ParagraphBlock, TableBlock
except ImportError:
    from api.models import (  # type: ignore
        HeadingBlock,
        KeyValueBlock,
        PageDocument,
        ParagraphBlock,
        TableBlock,
    )


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

    pdf.set_x(pdf.l_margin)
    width = pdf.epw

    if has_roboto:
        font_family = "Roboto"
        style = "B" if is_bold else ""
    else:
        font_family = "Helvetica"
        style = "B" if is_bold else ""
        text = text.encode("latin-1", "replace").decode("latin-1")

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


def _sanitize_table(
    headers: List[str],
    rows: List[List[str]],
) -> Tuple[List[str], List[List[str]], int]:
    """Cleans table cells, ensures consistent column count across all rows."""
    clean_headers = [str(h).strip() if h is not None else "" for h in headers]
    valid_rows = [
        [str(c).strip() if c is not None else "" for c in r]
        for r in rows
        if any(str(c).strip() for c in r if c is not None)
    ]

    num_cols = max(len(clean_headers), max((len(r) for r in valid_rows), default=0))
    if num_cols == 0:
        return [], [], 0

    if clean_headers:
        if len(clean_headers) < num_cols:
            clean_headers = clean_headers + [""] * (num_cols - len(clean_headers))
        elif len(clean_headers) > num_cols:
            clean_headers = clean_headers[:num_cols]

    padded_rows = []
    for r in valid_rows:
        if len(r) < num_cols:
            r = r + [""] * (num_cols - len(r))
        elif len(r) > num_cols:
            r = r[:num_cols]
        padded_rows.append(r)

    return clean_headers, padded_rows, num_cols


def _compute_col_widths(
    headers: List[str],
    rows: List[List[str]],
    num_cols: int,
) -> Optional[Tuple[float, ...]]:
    """Computes relative column width weights based on cell content length."""
    if num_cols <= 1:
        return None

    lengths = [len(h) for h in headers] if headers else [0] * num_cols
    for r in rows:
        for i, val in enumerate(r):
            if i < num_cols:
                lengths[i] = max(lengths[i], len(val))

    # Apply square root damping so long columns don't completely crush small columns like STT
    weights = [max(1.5, math.sqrt(max(1, l))) for l in lengths]
    total = sum(weights)
    if total <= 0:
        return None

    # Return normalized weights (summing to 100)
    return tuple((w / total) * 100.0 for w in weights)


def _render_table_block(pdf: CleanDocumentPDF, block: TableBlock, has_roboto: bool):
    """Renders a structured table with borders, proportional column widths, and cell padding."""
    headers, rows, num_cols = _sanitize_table(block.headers, block.rows)
    if num_cols == 0:
        return

    font_family = "Roboto" if has_roboto else "Helvetica"

    # Render table title if present
    if block.title and block.title.strip():
        pdf.set_text_color(20, 20, 20)
        _render_text_cell(
            pdf=pdf,
            text=block.title.strip(),
            font_size=10.5,
            is_bold=True,
            align="L",
            line_height=5.2,
            has_roboto=has_roboto,
        )
        pdf.ln(1.0)

    pdf.set_x(pdf.l_margin)
    col_widths = _compute_col_widths(headers, rows, num_cols)
    has_headers = bool(headers)

    try:
        pdf.set_font(font_family, "", 9.0)
        pdf.set_text_color(30, 30, 30)

        with pdf.table(
            col_widths=col_widths,
            first_row_as_headings=has_headers,
            borders_layout="ALL",
            cell_fill_color=(246, 248, 250),
            cell_fill_mode="ROWS",
            line_height=5.2,
            text_align="LEFT",
            padding=1.5,
        ) as table:
            if has_headers:
                header_row = table.row()
                for h in headers:
                    header_row.cell(h, align="C" if len(h) <= 5 else "L")

            for row_data in rows:
                r = table.row()
                for i, cell_val in enumerate(row_data):
                    # Center align very short codes / numbers like STT
                    cell_align = "C" if (i == 0 and len(cell_val) <= 4) else "L"
                    r.cell(cell_val, align=cell_align)

        pdf.ln(3.0)
        pdf.set_x(pdf.l_margin)
    except Exception as e:
        print(f"[PDF] Native table rendering encountered error, using fallback: {e}")
        # Robust fallback: render row-by-row as structured text lines
        if has_headers:
            _render_text_cell(
                pdf=pdf,
                text=" | ".join(headers),
                font_size=9.5,
                is_bold=True,
                align="L",
                line_height=5.0,
                has_roboto=has_roboto,
            )
        for r in rows:
            _render_text_cell(
                pdf=pdf,
                text=" | ".join(r),
                font_size=9.0,
                is_bold=False,
                align="L",
                line_height=4.8,
                has_roboto=has_roboto,
            )
        pdf.ln(2.0)


def _render_key_value_block(pdf: CleanDocumentPDF, block: KeyValueBlock, has_roboto: bool):
    """Renders a key-value label pair (e.g. 'Người đại diện: Nguyễn Văn A')."""
    label = block.label.strip()
    value = block.value.strip()
    if not label and not value:
        return

    if label and not label.endswith(":"):
        label = f"{label}:"

    combined_text = f"{label} {value}" if label else value
    pdf.set_text_color(30, 30, 30)
    _render_text_cell(
        pdf=pdf,
        text=combined_text,
        font_size=10.0,
        is_bold=False,
        align="L",
        line_height=5.2,
        has_roboto=has_roboto,
    )
    pdf.ln(1.0)


def _render_heading_block(pdf: CleanDocumentPDF, block: HeadingBlock, has_roboto: bool):
    """Renders a document heading or section title."""
    text = block.text.strip()
    if not text:
        return

    align_map = {"center": "C", "right": "R", "left": "L"}
    align = align_map.get(block.align.lower(), "L")

    if block.level == 1:
        pdf.ln(1.5)
        pdf.set_text_color(15, 15, 15)
        _render_text_cell(
            pdf=pdf,
            text=text,
            font_size=13.0,
            is_bold=True,
            align=align,
            line_height=6.5,
            has_roboto=has_roboto,
        )
        pdf.ln(2.0)
    elif block.level == 2:
        pdf.ln(1.5)
        pdf.set_text_color(25, 25, 25)
        _render_text_cell(
            pdf=pdf,
            text=text,
            font_size=11.0,
            is_bold=True,
            align=align,
            line_height=5.8,
            has_roboto=has_roboto,
        )
        pdf.ln(1.0)
    else:
        pdf.set_text_color(30, 30, 30)
        _render_text_cell(
            pdf=pdf,
            text=text,
            font_size=10.5,
            is_bold=True,
            align=align,
            line_height=5.4,
            has_roboto=has_roboto,
        )
        pdf.ln(0.5)


def _clean_paragraph_text(text: str) -> str:
    """Unwraps soft line breaks within paragraphs while preserving genuine paragraph breaks."""
    raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    cleaned_paras = []
    for p in raw_paragraphs:
        lines = [line.strip() for line in p.split("\n") if line.strip()]
        cleaned_paras.append(" ".join(lines))
    return "\n\n".join(cleaned_paras)


def _render_paragraph_block(pdf: CleanDocumentPDF, block: ParagraphBlock, has_roboto: bool):
    """Renders a standard text paragraph with justified alignment and elegant book spacing."""
    text = block.text.strip()
    if not text:
        pdf.ln(2.5)
        return

    cleaned_text = _clean_paragraph_text(text)
    paragraphs = [p.strip() for p in cleaned_text.split("\n\n") if p.strip()]

    pdf.set_text_color(30, 30, 30)
    for p in paragraphs:
        _render_text_cell(
            pdf=pdf,
            text=p,
            font_size=10.5,
            is_bold=False,
            align="J",
            line_height=5.8,
            has_roboto=has_roboto,
        )
        pdf.ln(3.0)  # Professional paragraph spacing


def _render_structured_page(pdf: CleanDocumentPDF, page_doc: PageDocument, has_roboto: bool):
    """Renders a page composed of typed blocks (headings, paragraphs, tables, key-values)."""
    for block in page_doc.blocks:
        b_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)

        if b_type == "table":
            if isinstance(block, dict):
                block = TableBlock.model_validate(block)
            _render_table_block(pdf, block, has_roboto)
        elif b_type == "heading":
            if isinstance(block, dict):
                block = HeadingBlock.model_validate(block)
            _render_heading_block(pdf, block, has_roboto)
        elif b_type == "key_value":
            if isinstance(block, dict):
                block = KeyValueBlock.model_validate(block)
            _render_key_value_block(pdf, block, has_roboto)
        elif b_type == "paragraph":
            if isinstance(block, dict):
                block = ParagraphBlock.model_validate(block)
            _render_paragraph_block(pdf, block, has_roboto)
        else:
            # Fallback for unrecognized block
            val = getattr(block, "text", "") or str(block)
            if val.strip():
                _render_text_cell(pdf, val, has_roboto=has_roboto)


def _render_legacy_text_page(pdf: CleanDocumentPDF, text: str, has_roboto: bool):
    """
    Legacy parser for raw OCR text lines.
    Reassembles consecutive lines into flowing, justified paragraphs instead of
    chopped line fragments, while preserving mottos and headings.
    """
    lines = text.split("\n")
    paragraph_buffer: List[str] = []

    def flush_paragraph():
        if paragraph_buffer:
            combined = " ".join(paragraph_buffer).strip()
            if combined:
                pdf.set_text_color(30, 30, 30)
                _render_text_cell(
                    pdf=pdf,
                    text=combined,
                    font_size=10.5,
                    is_bold=False,
                    align="J",
                    line_height=5.8,
                    has_roboto=has_roboto,
                )
                pdf.ln(3.0)
            paragraph_buffer.clear()

    for line in lines:
        line_str = line.strip()
        if not line_str:
            flush_paragraph()
            pdf.ln(1.5)
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
            or line_str.startswith("Phần ")
            or line_str.startswith("PHẦN ")
        )

        if is_contract_title:
            flush_paragraph()
            pdf.ln(1.5)
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
            pdf.ln(2.0)
        elif is_national_motto:
            flush_paragraph()
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
            pdf.ln(1.5)
        elif is_article_heading:
            flush_paragraph()
            pdf.ln(2.0)
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
            pdf.ln(1.0)
        else:
            paragraph_buffer.append(line_str)

    flush_paragraph()


def build_pdf(
    image_bytes_list: List[bytes],
    ocr_results: List[Union[PageDocument, dict, str, None]],
) -> bytes:
    """
    Builds a clean, freshly digitized PDF document supporting both:
    1. Structured PageDocument blocks (tables, headings, key-values, paragraphs).
    2. Raw string text fallback from Cloud Vision OCR.

    - Pure white A4 background.
    - TrueType Unicode typography (Roboto) supporting Vietnamese & English.
    - Native table grids with alternating row backgrounds and clean borders.
    - Automatic page numbering.

    Returns the generated PDF as raw bytes.
    """
    pdf = CleanDocumentPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)

    has_roboto = False
    if os.path.exists(REGULAR_FONT):
        try:
            pdf.add_font("Roboto", "", REGULAR_FONT)
            if os.path.exists(BOLD_FONT):
                pdf.add_font("Roboto", "B", BOLD_FONT)
            has_roboto = True
        except Exception as e:
            print(f"[PDF] Failed to load Roboto: {e}")

    for idx, page_data in enumerate(ocr_results):
        pdf.add_page()
        pdf.set_x(pdf.l_margin)

        if page_data is None:
            _render_text_cell(
                pdf=pdf,
                text="[Không nhận diện được nội dung văn bản]",
                font_size=10.0,
                align="C",
                has_roboto=has_roboto,
            )
            continue

        if isinstance(page_data, PageDocument):
            _render_structured_page(pdf, page_data, has_roboto)
        elif isinstance(page_data, dict) and "blocks" in page_data:
            doc = PageDocument.model_validate(page_data)
            _render_structured_page(pdf, doc, has_roboto)
        elif isinstance(page_data, str):
            _render_legacy_text_page(pdf, page_data, has_roboto)
        else:
            _render_text_cell(pdf, str(page_data), has_roboto=has_roboto)

    return bytes(pdf.output())
