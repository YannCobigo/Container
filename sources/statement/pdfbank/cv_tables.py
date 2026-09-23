from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

from .models import BBox, ExtractedTable, NormalizedPage, TableCell
from .utils import union_bbox


def _render_gray(pdf_path: str | Path, page_number: int, dpi: int = 200):
    doc = fitz.open(str(pdf_path))
    page = doc[page_number - 1]
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY)
    else:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    dims = (float(page.rect.width), float(page.rect.height), float(pix.width), float(pix.height))
    doc.close()
    return gray, dims


def detect_bordered_tables(pdf_path: str | Path, page: NormalizedPage, dpi: int = 200) -> list[ExtractedTable]:
    """Detect ruled table cells using morphology, then populate them from normalized words.

    This is deliberately conservative: it returns no table if a credible grid cannot be found.
    """
    gray, (width_pt, height_pt, pix_w, pix_h) = _render_gray(pdf_path, page.page_number, dpi=dpi)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    h, w = bw.shape[:2]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 25), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 35)))
    horizontal = cv2.morphologyEx(bw, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vertical_kernel)
    grid = cv2.bitwise_or(horizontal, vertical)
    contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    sx, sy = width_pt / pix_w, height_pt / pix_h
    raw_cells: list[BBox] = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw < 25 or ch < 10 or cw > w * 0.98 or ch > h * 0.75:
            continue
        area = cw * ch
        if area < 400:
            continue
        raw_cells.append((x * sx, y * sy, (x + cw) * sx, (y + ch) * sy))

    # Deduplicate nested/near-identical contours.
    raw_cells.sort(key=lambda b: (b[1], b[0], (b[2] - b[0]) * (b[3] - b[1])))
    cells: list[BBox] = []
    for box in raw_cells:
        if any(abs(box[0] - c[0]) < 2 and abs(box[1] - c[1]) < 2 and abs(box[2] - c[2]) < 2 and abs(box[3] - c[3]) < 2 for c in cells):
            continue
        cells.append(box)
    if len(cells) < 6:
        return []

    # Group by y center and derive row/column indexes.
    row_groups: list[list[BBox]] = []
    for box in sorted(cells, key=lambda b: ((b[1] + b[3]) / 2, b[0])):
        cy = (box[1] + box[3]) / 2
        match = None
        for idx, group in enumerate(row_groups):
            gy = sum((b[1] + b[3]) / 2 for b in group) / len(group)
            if abs(cy - gy) <= max(4.0, (box[3] - box[1]) * 0.45):
                match = idx
                break
        if match is None:
            row_groups.append([box])
        else:
            row_groups[match].append(box)
    row_groups = [sorted(g, key=lambda b: b[0]) for g in row_groups if len(g) >= 2]
    if len(row_groups) < 2:
        return []

    out_cells: list[TableCell] = []
    rows: list[list[str]] = []
    for r_idx, group in enumerate(row_groups):
        row_text: list[str] = []
        for c_idx, box in enumerate(group):
            contained = [
                word for word in page.words
                if (box[0] - 2) <= (word.bbox[0] + word.bbox[2]) / 2 <= (box[2] + 2)
                and (box[1] - 2) <= (word.bbox[1] + word.bbox[3]) / 2 <= (box[3] + 2)
            ]
            contained.sort(key=lambda w: (w.bbox[1], w.bbox[0]))
            text = " ".join(wd.text for wd in contained)
            confidence = sum(wd.confidence for wd in contained) / len(contained) if contained else 0.5
            out_cells.append(TableCell(row=r_idx, column=c_idx, text=text, bbox=box, confidence=confidence))
            row_text.append(text)
        rows.append(row_text)

    nonempty = sum(bool(c.text.strip()) for c in out_cells)
    if nonempty < 4:
        return []
    return [
        ExtractedTable(
            page_number=page.page_number,
            rows=rows,
            cells=out_cells,
            bbox=union_bbox(c.bbox for c in out_cells if c.bbox),
            source_text="\n".join(" | ".join(r) for r in rows),
            extraction_method="opencv_ruled_grid",
            confidence=min(0.92, 0.58 + min(nonempty, 40) * 0.008),
        )
    ]
