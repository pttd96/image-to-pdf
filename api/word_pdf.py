import math
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from fpdf import FPDF

FONT_DIR = Path(__file__).parent / "fonts"
REGULAR_FONT = str(FONT_DIR / "Roboto-Regular.ttf")
BOLD_FONT = str(FONT_DIR / "Roboto-Bold.ttf")


class WordDocumentPDF(FPDF):
    """
    FPDF subclass configured to emulate a Microsoft Word / Google Docs exported PDF:
    - Standard A4 page geometry with 20mm margins.
    - Clean running page footer with page numbers ('Trang X / Y').
    """

    def footer(self):
        self.set_y(-15)
        if "Roboto" in self.fonts:
            self.set_font("Roboto", "", 9)
        else:
            self.set_font("Helvetica", "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Trang {self.page_no()} / {{nb}}", align="C")


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    # Check if inside is dashes and colons
    inner = stripped[1:-1].replace("|", "").replace("-", "").replace(":", "").replace(" ", "")
    return len(inner) == 0


def _parse_table_cells(line: str) -> List[str]:
    parts = line.strip().split("|")[1:-1]
    return [p.strip() for p in parts]


def _compute_col_widths(
    headers: List[str],
    rows: List[List[str]],
    num_cols: int,
) -> Optional[Tuple[float, ...]]:
    if num_cols <= 1:
        return None

    lengths = [len(h) for h in headers] if headers else [0] * num_cols
    for r in rows:
        for i, val in enumerate(r):
            if i < num_cols:
                lengths[i] = max(lengths[i], len(val))

    weights = [max(1.8, math.sqrt(max(1, l))) for l in lengths]
    total = sum(weights)
    if total <= 0:
        return None
    return tuple((w / total) * 100.0 for w in weights)


def _render_word_table(
    pdf: WordDocumentPDF,
    headers: List[str],
    rows: List[List[str]],
    has_roboto: bool,
    is_signature_block: bool = False,
):
    font_family = "Roboto" if has_roboto else "Helvetica"
    num_cols = max(len(headers), max((len(r) for r in rows), default=0))
    if num_cols == 0:
        return

    # Normalize row lengths
    clean_headers = headers + [""] * (num_cols - len(headers)) if headers else []
    clean_rows = [r + [""] * (num_cols - len(r)) for r in rows]

    pdf.set_x(pdf.l_margin)

    if is_signature_block:
        # Render borderless signature columns with vertical space for signing
        pdf.ln(5.0)
        pdf.set_font(font_family, "B", 10.5)
        pdf.set_text_color(20, 20, 20)

        with pdf.table(
            first_row_as_headings=False,
            borders_layout="NONE",
            line_height=5.5,
            text_align="CENTER",
            padding=1.0,
        ) as table:
            # Header line: e.g. ĐẠI DIỆN BÊN A | ĐẠI DIỆN BÊN B
            r_head = table.row()
            for h in (clean_headers if clean_headers else clean_rows[0]):
                r_head.cell(h, align="C")

            # Subsequent rows: e.g. (Ký, ghi rõ họ tên)
            for row_data in (clean_rows if clean_headers else clean_rows[1:]):
                r = table.row()
                for c in row_data:
                    r.cell(c, align="C")

        # Generous vertical room for physical signatures / stamps
        pdf.ln(22.0)
        pdf.set_x(pdf.l_margin)
        return

    # Standard Word-Style Table
    col_widths = _compute_col_widths(clean_headers, clean_rows, num_cols)
    pdf.set_draw_color(200, 205, 215)  # Thin gray Word border
    pdf.set_line_width(0.2)

    try:
        with pdf.table(
            col_widths=col_widths,
            first_row_as_headings=bool(clean_headers),
            borders_layout="ALL",
            cell_fill_color=(242, 244, 248),  # Subtle Word header tint
            cell_fill_mode="ROWS",
            line_height=5.2,
            text_align="LEFT",
            padding=1.8,
        ) as table:
            if clean_headers:
                pdf.set_font(font_family, "B", 9.5)
                pdf.set_text_color(30, 41, 59)
                h_row = table.row()
                for i, h in enumerate(clean_headers):
                    align = "C" if (i == 0 and len(h) <= 5) or len(h) <= 4 else "L"
                    h_row.cell(h, align=align)

            pdf.set_font(font_family, "", 9.0)
            pdf.set_text_color(51, 65, 85)
            for r_data in clean_rows:
                r = table.row()
                for i, cell_val in enumerate(r_data):
                    align = "C" if (i == 0 and len(cell_val) <= 4) else "L"
                    r.cell(cell_val, align=align)

        pdf.ln(3.5)
        pdf.set_x(pdf.l_margin)
    except Exception as e:
        print(f"[WORD_PDF] Table render error: {e}")
        pdf.set_x(pdf.l_margin)


def render_markdown_page(pdf: WordDocumentPDF, markdown_text: str, has_roboto: bool):
    """
    Renders a page of Markdown formatted according to Microsoft Word document typography rules.
    """
    font_family = "Roboto" if has_roboto else "Helvetica"
    lines = markdown_text.strip().split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line:
            pdf.ln(2.0)
            i += 1
            continue

        # 1. Centered Mottos / Titles (e.g. <center># CỘNG HÒA...</center> or <center>**...**</center>)
        if line.startswith("<center>") and line.endswith("</center>"):
            inner = line[8:-9].strip()
            # Strip markdown bold / header inside center
            is_bold = inner.startswith("**") or inner.startswith("#")
            clean_text = inner.replace("**", "").replace("#", "").strip()

            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "B" if is_bold else "", 12.0)
            pdf.set_text_color(15, 23, 42)
            pdf.multi_cell(
                w=pdf.epw,
                h=6.0,
                text=clean_text,
                align="C",
                wrapmode="WORD",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(1.5)
            i += 1
            continue

        # 2. Main Document Title (# Heading 1)
        if line.startswith("# "):
            text = line[2:].strip().replace("**", "")
            pdf.ln(2.0)
            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "B", 14.5)
            pdf.set_text_color(15, 23, 42)
            pdf.multi_cell(
                w=pdf.epw,
                h=7.0,
                text=text,
                align="C" if any(kw in text.upper() for kw in ["HỢP ĐỒNG", "QUYẾT ĐỊNH", "CỘNG HÒA", "BIÊN BẢN"]) else "L",
                wrapmode="WORD",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(2.5)
            i += 1
            continue

        # 3. Section Heading (## Heading 2)
        if line.startswith("## "):
            text = line[3:].strip().replace("**", "")
            pdf.ln(2.0)
            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "B", 11.5)
            pdf.set_text_color(30, 41, 59)
            pdf.multi_cell(
                w=pdf.epw,
                h=6.0,
                text=text,
                align="L",
                wrapmode="WORD",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(1.5)
            i += 1
            continue

        # 4. Subsection Heading (### Heading 3)
        if line.startswith("### "):
            text = line[4:].strip().replace("**", "")
            pdf.ln(1.5)
            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "B", 10.5)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(
                w=pdf.epw,
                h=5.5,
                text=text,
                align="L",
                wrapmode="WORD",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(1.0)
            i += 1
            continue

        # 5. Tables & 2-Column Signature Blocks
        if _is_table_row(line):
            table_lines: List[str] = []
            while i < len(lines) and (_is_table_row(lines[i]) or _is_table_separator(lines[i])):
                if not _is_table_separator(lines[i]):
                    table_lines.append(lines[i].strip())
                i += 1

            if table_lines:
                headers = _parse_table_cells(table_lines[0])
                rows = [_parse_table_cells(tl) for tl in table_lines[1:]]

                # Check if this table represents a signature block (e.g. 2 columns with Bên A / Bên B / Ký)
                is_sig = False
                header_text = " ".join(headers).upper()
                if len(headers) == 2 and any(kw in header_text for kw in ["BÊN A", "BÊN B", "ĐẠI DIỆN", "KÝ"]):
                    is_sig = True

                _render_word_table(
                    pdf=pdf,
                    headers=headers,
                    rows=rows,
                    has_roboto=has_roboto,
                    is_signature_block=is_sig,
                )
            continue

        # 6. Bullet lists (* or -)
        if line.startswith("* ") or line.startswith("- "):
            text = line[2:].strip()
            pdf.set_x(pdf.l_margin)
            pdf.set_font(font_family, "", 10.5)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(
                w=pdf.epw,
                h=5.6,
                text=f"  •  {text}",
                align="L",
                wrapmode="WORD",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(1.2)
            i += 1
            continue

        # 7. Standard Body Paragraphs (Flowing, Justified)
        paragraph_buffer = [line]
        i += 1
        while i < len(lines):
            next_l = lines[i].strip()
            if not next_l or next_l.startswith("#") or next_l.startswith("<center>") or _is_table_row(next_l) or next_l.startswith("* ") or next_l.startswith("- "):
                break
            paragraph_buffer.append(next_l)
            i += 1

        full_para = " ".join(paragraph_buffer).strip()
        # Clean double spaces
        full_para = re.sub(r"\s+", " ", full_para)

        pdf.set_x(pdf.l_margin)
        pdf.set_font(font_family, "", 10.5)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(
            w=pdf.epw,
            h=5.6,
            text=full_para,
            align="J",
            wrapmode="WORD",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(2.5)  # Paragraph margin


def build_word_style_pdf(pages_markdown: List[str]) -> bytes:
    """
    Assembles multiple pages of Word-styled Markdown into a single, clean vector PDF.
    """
    pdf = WordDocumentPDF(orientation="P", unit="mm", format="A4")
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
            print(f"[WORD_PDF] Failed to load Roboto: {e}")

    for idx, page_md in enumerate(pages_markdown):
        pdf.add_page()
        pdf.set_x(pdf.l_margin)

        if not page_md or not page_md.strip():
            pdf.set_font("Roboto" if has_roboto else "Helvetica", "", 10.0)
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 10, "[Không có nội dung nhận diện]", align="C")
            continue

        render_markdown_page(pdf, page_md, has_roboto)

    return bytes(pdf.output())
