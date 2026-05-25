"""One-page A4 PDF receipt generator.

Layout (portrait A4, 210mm x 297mm):
    1. Header line ("Botji - Render Receipt") centred, 14pt bold.
    2. Two 80x80mm thumbnails side-by-side (source left, output right).
    3. Three labelled columns: Preserved / Changed / Generated, <=5 bullets each.
    4. 6-row x 2-col table titled "Fidelity Scores".
    5. Footer: C2PA manifest ref + UTC timestamp + signature line.

Telegram/agent pipelines often hand us palette-mode PNGs; fpdf2's PNG decoder
chokes on those, so every image is normalised via Pillow to RGB/RGBA before
embedding. Normalised copies live next to the generated PDF and are reused
across regenerations.
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any

from fpdf import FPDF  # type: ignore[import-not-found]
from PIL import Image

logger = logging.getLogger(__name__)

_PAGE_W_MM = 210.0
_MARGIN_MM = 15.0
_THUMB_MM = 80.0
_COL_GAP_MM = 6.0


_ASCII_FALLBACK = {
    "→": "->", "←": "<-", "↔": "<->",  # arrows
    "‘": "'", "’": "'", "“": '"', "”": '"',  # smart quotes
    "–": "-", "—": "-", "•": "*", "…": "...",  # dashes / bullet / ellipsis
    " ": " ", " ": " ",  # spaces
}


def _safe(text: Any) -> str:
    """Helvetica is Latin-1 only; fold common Unicode to ASCII and drop the rest."""
    raw = str(text or "")
    for src, dst in _ASCII_FALLBACK.items():
        raw = raw.replace(src, dst)
    return raw.encode("latin-1", errors="replace").decode("latin-1")


def _normalize_image(path: Path, dest: Path) -> Path:
    """Convert palette/grayscale PNGs to RGB(A) so fpdf2 can embed them."""
    with Image.open(path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "PNG")
    return dest


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bullets(pdf: FPDF, items: list[str], x: float, y: float, w: float) -> None:
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "", 9)
    for item in (items or [])[:5]:
        text = _safe(item).strip()
        if not text:
            continue
        pdf.set_xy(x, pdf.get_y())
        pdf.multi_cell(w, 4.5, f"- {text}", border=0)
    if not items:
        pdf.set_xy(x, y)
        pdf.set_text_color(140, 140, 140)
        pdf.cell(w, 4.5, "(none)", border=0)
        pdf.set_text_color(0, 0, 0)


def _column_header(pdf: FPDF, label: str, x: float, y: float, w: float) -> None:
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(w, 5, _safe(label), border="B", align="L")


def _draw_thumbnails(pdf: FPDF, src_path: Path, out_path: Path, y: float) -> float:
    inner_w = _PAGE_W_MM - 2 * _MARGIN_MM
    left_x = _MARGIN_MM + (inner_w - 2 * _THUMB_MM - _COL_GAP_MM) / 2
    right_x = left_x + _THUMB_MM + _COL_GAP_MM
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(left_x, y)
    pdf.cell(_THUMB_MM, 4, _safe("Source"), align="C")
    pdf.set_xy(right_x, y)
    pdf.cell(_THUMB_MM, 4, _safe("Output"), align="C")
    img_y = y + 5
    pdf.image(str(src_path), x=left_x, y=img_y, w=_THUMB_MM, h=_THUMB_MM, keep_aspect_ratio=True)
    pdf.image(str(out_path), x=right_x, y=img_y, w=_THUMB_MM, h=_THUMB_MM, keep_aspect_ratio=True)
    return img_y + _THUMB_MM


def _draw_columns(pdf: FPDF, receipt: dict[str, Any], y: float) -> float:
    inner_w = _PAGE_W_MM - 2 * _MARGIN_MM
    col_w = (inner_w - 2 * _COL_GAP_MM) / 3
    sources = [
        ("Preserved", receipt.get("preserved") or []),
        ("Changed", receipt.get("changed") or []),
        ("Generated", receipt.get("generated") or []),
    ]
    for i, (label, items) in enumerate(sources):
        x = _MARGIN_MM + i * (col_w + _COL_GAP_MM)
        _column_header(pdf, label, x, y, col_w)
        _bullets(pdf, items, x, y + 6, col_w)
    return y + 6 + 5 * 4.5 + 4  # header + 5 bullet lines + padding


def _draw_scores(pdf: FPDF, receipt: dict[str, Any], y: float) -> float:
    inner_w = _PAGE_W_MM - 2 * _MARGIN_MM
    pdf.set_xy(_MARGIN_MM, y)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(inner_w, 6, _safe("Fidelity Scores"), border=0)
    y += 7
    pdf.set_font("Helvetica", "", 9)
    scores = receipt.get("fidelity_scores")
    if not isinstance(scores, dict) or not scores:
        scores = {"(no scores)": "-"}
    rows = list(scores.items())[:6]
    while len(rows) < 6:
        rows.append(("", ""))
    name_w = inner_w * 0.6
    val_w = inner_w - name_w
    for name, value in rows:
        pdf.set_xy(_MARGIN_MM, y)
        pdf.cell(name_w, 5.5, _safe(name), border=1)
        pdf.cell(val_w, 5.5, _safe(value), border=1, align="R")
        y += 5.5
    return y


def _draw_footer(pdf: FPDF, c2pa_ref: str | None) -> None:
    pdf.set_y(-22)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 4, _safe(f"C2PA: {c2pa_ref or 'not present'}"), ln=1)
    pdf.cell(0, 4, _safe(f"Generated: {_utc_stamp()}"), ln=1)
    pdf.cell(0, 4, _safe("Signature: __________________________________________________"), ln=1)
    pdf.set_text_color(0, 0, 0)


def generate_receipt_pdf(
    receipt: dict[str, Any],
    source_image_path: str,
    output_image_path: str,
    output_dir: Path,
    c2pa_ref: str | None = None,
) -> Path:
    """Render the one-page A4 receipt PDF and return its path.

    Caller is responsible for path safety on ``output_dir``; the receipt
    file is written under it as ``receipt.pdf``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    src_norm = _normalize_image(Path(source_image_path), output_dir / "_src.png")
    out_norm = _normalize_image(Path(output_image_path), output_dir / "_out.png")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(_MARGIN_MM, _MARGIN_MM, _MARGIN_MM)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _safe("Botji - Render Receipt"), border=0, align="C", ln=1)

    receipt_id = str(receipt.get("receipt_id") or "")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 4, _safe(f"Receipt: {receipt_id}  |  Route: {receipt.get('route', '-')}  |  Status: {receipt.get('status', '-')}"),
             border=0, align="C", ln=1)

    after_thumbs = _draw_thumbnails(pdf, src_norm, out_norm, y=pdf.get_y() + 4)
    after_cols = _draw_columns(pdf, receipt, y=after_thumbs + 6)
    _draw_scores(pdf, receipt, y=after_cols + 4)
    _draw_footer(pdf, c2pa_ref)

    pdf_path = output_dir / "receipt.pdf"
    pdf.set_title(f"Botji Receipt {receipt_id}")
    pdf.set_author("Botji")
    pdf.output(str(pdf_path))
    logger.info("botji-receipt: wrote %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
    return pdf_path


__all__ = ["generate_receipt_pdf"]
