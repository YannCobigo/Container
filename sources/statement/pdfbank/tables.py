from __future__ import annotations

from pathlib import Path
import statistics

from .models import ExtractedTable, NormalizedPage, TableCell
from .utils import DATE_RE, MONEY_RE, union_bbox
from .cv_tables import detect_bordered_tables


class TableExtractor:
    def __init__(self, enable_camelot: bool = True) -> None:
        self.enable_camelot = enable_camelot

    @staticmethod
    def _table_from_pdfplumber(pdf_path: str | Path, page_number: int) -> list[ExtractedTable]:
        import pdfplumber

        results: list[ExtractedTable] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[page_number - 1]
            settings_options = [
                {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                {"vertical_strategy": "text", "horizontal_strategy": "text", "snap_tolerance": 4, "join_tolerance": 4},
            ]
            for settings in settings_options:
                try:
                    tables = page.find_tables(table_settings=settings)
                except Exception:
                    continue
                for table in tables:
                    data = table.extract() or []
                    rows = [[(cell or "").strip() for cell in row] for row in data]
                    nonempty = sum(bool(c) for row in rows for c in row)
                    if len(rows) < 2 or nonempty < 4:
                        continue
                    bbox = tuple(float(v) for v in table.bbox)
                    conf = min(0.95, 0.55 + min(len(rows), 20) * 0.015 + min(nonempty, 60) * 0.003)
                    results.append(
                        ExtractedTable(
                            page_number=page_number,
                            rows=rows,
                            bbox=bbox,
                            source_text="\n".join(" | ".join(r) for r in rows),
                            extraction_method="pdfplumber_lines" if settings["vertical_strategy"] == "lines" else "pdfplumber_text",
                            confidence=conf,
                        )
                    )
                if results:
                    break
        return results

    def _table_from_camelot(self, pdf_path: str | Path, page_number: int) -> list[ExtractedTable]:
        if not self.enable_camelot:
            return []
        try:
            import camelot
        except Exception:
            return []
        out: list[ExtractedTable] = []
        for flavor in ("lattice", "stream"):
            try:
                tables = camelot.read_pdf(str(pdf_path), pages=str(page_number), flavor=flavor, suppress_stdout=True)
            except Exception:
                continue
            for table in tables:
                rows = [[str(v).strip() for v in row] for row in table.df.values.tolist()]
                if len(rows) < 2:
                    continue
                accuracy = float(getattr(table, "accuracy", 70.0)) / 100.0
                out.append(
                    ExtractedTable(
                        page_number=page_number,
                        rows=rows,
                        bbox=None,
                        source_text="\n".join(" | ".join(r) for r in rows),
                        extraction_method=f"camelot_{flavor}",
                        confidence=max(0.2, min(0.98, accuracy)),
                    )
                )
            if out:
                break
        return out

    @staticmethod
    def _table_from_normalized_lines(page: NormalizedPage) -> list[ExtractedTable]:
        """Borderless/OCR fallback: identify repeated transaction-like rows by geometry."""
        candidate_lines = []
        for line in page.lines:
            if DATE_RE.search(line.text) and MONEY_RE.search(line.text):
                candidate_lines.append(line)
        if len(candidate_lines) < 2:
            return []

        # Construct columns from large x-gaps across transaction rows. This preserves cell bboxes.
        gaps: list[float] = []
        for line in candidate_lines:
            ws = sorted(line.words, key=lambda w: w.bbox[0])
            for a, b in zip(ws, ws[1:]):
                gap = b.bbox[0] - a.bbox[2]
                if gap > 10:
                    gaps.append((a.bbox[2] + b.bbox[0]) / 2)
        separators: list[float] = []
        for x in sorted(gaps):
            if not separators or abs(x - separators[-1]) > 18:
                separators.append(x)
            else:
                separators[-1] = (separators[-1] + x) / 2
        separators = separators[:5]

        rows: list[list[str]] = []
        cells: list[TableCell] = []
        for r_idx, line in enumerate(candidate_lines):
            buckets: list[list] = [[] for _ in range(len(separators) + 1)]
            for word in line.words:
                cx = (word.bbox[0] + word.bbox[2]) / 2
                col = sum(cx > s for s in separators)
                buckets[col].append(word)
            row: list[str] = []
            for c_idx, bucket in enumerate(buckets):
                bucket = sorted(bucket, key=lambda w: w.bbox[0])
                text = " ".join(w.text for w in bucket)
                row.append(text)
                cells.append(
                    TableCell(
                        row=r_idx,
                        column=c_idx,
                        text=text,
                        bbox=union_bbox(w.bbox for w in bucket),
                        confidence=sum(w.confidence for w in bucket) / len(bucket) if bucket else 0.0,
                    )
                )
            rows.append(row)
        return [
            ExtractedTable(
                page_number=page.page_number,
                rows=rows,
                cells=cells,
                bbox=union_bbox(line.bbox for line in candidate_lines),
                source_text="\n".join(line.text for line in candidate_lines),
                extraction_method="ocr_geometry" if page.selected_source.startswith("tesseract") else "native_geometry_borderless",
                confidence=min(0.90, 0.55 + len(candidate_lines) * 0.04),
            )
        ]

    def extract(self, pdf_path: str | Path, page: NormalizedPage, table_likelihood: float = 0.0) -> list[ExtractedTable]:
        results: list[ExtractedTable] = []
        if page.selected_source.startswith("native"):
            if table_likelihood >= 0.18:
                try:
                    results.extend(self._table_from_pdfplumber(pdf_path, page.page_number))
                except Exception:
                    pass
            if not results and table_likelihood >= 0.30:
                results.extend(self._table_from_camelot(pdf_path, page.page_number))
        if not results and page.selected_source.startswith("tesseract") and table_likelihood >= 0.12:
            try:
                results.extend(detect_bordered_tables(pdf_path, page))
            except Exception:
                pass
        if not results:
            results.extend(self._table_from_normalized_lines(page))
        return results
