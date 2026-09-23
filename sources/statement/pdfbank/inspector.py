from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from pypdf import PdfReader

from .models import DocumentInspection, PageInspection, Word
from .utils import bbox_area, clamp01, financial_text_score, sha256_file


class DocumentInspector:
    """Non-destructive structural inspection used to choose page processing paths."""

    def __init__(
        self,
        reliable_native_chars: int = 80,
        scanned_image_ratio: float = 0.55,
        ocr_native_char_floor: int = 24,
    ) -> None:
        self.reliable_native_chars = reliable_native_chars
        self.scanned_image_ratio = scanned_image_ratio
        self.ocr_native_char_floor = ocr_native_char_floor

    @staticmethod
    def _native_words(page: fitz.Page) -> list[Word]:
        words: list[Word] = []
        for item in page.get_text("words", sort=True):
            x0, y0, x1, y1, text, block_no, line_no, _word_no = item[:8]
            if str(text).strip():
                words.append(
                    Word(
                        text=str(text),
                        bbox=(float(x0), float(y0), float(x1), float(y1)),
                        confidence=1.0,
                        source="native_pdf_text",
                        block_id=int(block_no),
                        line_id=int(line_no),
                    )
                )
        return words

    def inspect(self, path: str | Path) -> DocumentInspection:
        path = Path(path)
        encrypted = False
        metadata: dict[str, Any] = {}
        try:
            reader = PdfReader(str(path), strict=False)
            encrypted = bool(reader.is_encrypted)
            if reader.metadata:
                metadata.update({str(k): str(v) for k, v in reader.metadata.items() if v is not None})
        except Exception as exc:
            metadata["pypdf_warning"] = type(exc).__name__

        doc = fitz.open(str(path))
        if doc.needs_pass:
            encrypted = True
            if not doc.authenticate(""):
                page_count = int(doc.page_count)
                metadata["encryption_status"] = "locked"
                doc.close()
                return DocumentInspection(
                    source_filename=path.name, sha256=sha256_file(path), page_count=page_count,
                    encrypted=True, metadata=metadata, pages=[], document_kind="unknown",
                    financial_statement_likelihood=0.0, appears_financial_statement=False,
                )
        metadata.update({k: v for k, v in doc.metadata.items() if v and k not in metadata})

        page_results: list[PageInspection] = []
        all_text: list[str] = []
        for idx, page in enumerate(doc):
            width, height = float(page.rect.width), float(page.rect.height)
            page_area = max(1.0, width * height)
            native_words = self._native_words(page)
            native_text = " ".join(w.text for w in native_words)
            all_text.append(native_text)
            char_count = sum(len(w.text) for w in native_words)
            # chars per 100 square points: stable relative signal, not physical density.
            text_density = char_count / page_area * 10000.0

            image_area = 0.0
            image_dpis: list[float] = []
            images = page.get_images(full=True)
            for image in images:
                xref = image[0]
                px_w = int(image[2]) if len(image) > 2 else 0
                px_h = int(image[3]) if len(image) > 3 else 0
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                for r in rects:
                    b = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
                    area = bbox_area(b)
                    image_area += area
                    if r.width > 1 and r.height > 1 and px_w and px_h:
                        dpi_x = px_w / (r.width / 72.0)
                        dpi_y = px_h / (r.height / 72.0)
                        image_dpis.append(min(dpi_x, dpi_y))
            image_ratio = clamp01(image_area / page_area)
            image_dpi = sum(image_dpis) / len(image_dpis) if image_dpis else None

            # Table signal: vector ruling + transaction-like lexical/numeric structure.
            drawings = page.get_drawings()
            rule_count = 0
            for drawing in drawings:
                for it in drawing.get("items", []):
                    if it and it[0] in {"l", "re"}:
                        rule_count += 1
            lower = native_text.lower()
            tabular_terms = sum(k in lower for k in ["date", "amount", "debit", "credit", "balance", "description"])
            numeric_tokens = sum(any(c.isdigit() for c in w.text) for w in native_words)
            table_likelihood = clamp01(min(rule_count, 20) / 20 * 0.45 + min(tabular_terms, 5) / 5 * 0.35 + min(numeric_tokens, 30) / 30 * 0.20)

            reliable_text = char_count >= self.reliable_native_chars and text_density >= 1.0
            scanned_likelihood = clamp01(
                image_ratio * 0.65
                + (1.0 if char_count < self.ocr_native_char_floor else 0.0) * 0.30
                + (0.05 if len(images) else 0.0)
            )
            # An image-dominant page with substantial invisible/embedded text often already has OCR.
            ocr_present = clamp01(image_ratio * 0.55 + min(char_count / 400.0, 1.0) * 0.45) if images else 0.0
            needs_ocr = not reliable_text and (image_ratio >= 0.20 or char_count < self.ocr_native_char_floor)

            if reliable_text and image_ratio < self.scanned_image_ratio:
                kind = "digital"
            elif not reliable_text and image_ratio >= self.scanned_image_ratio:
                kind = "scanned"
            elif image_ratio >= 0.20 and char_count > 0:
                kind = "mixed"
            else:
                kind = "unknown"

            page_results.append(
                PageInspection(
                    page_number=idx + 1,
                    width_pt=width,
                    height_pt=height,
                    rotation=int(page.rotation or 0),
                    native_char_count=char_count,
                    text_density=round(text_density, 4),
                    image_count=len(images),
                    image_area_ratio=round(image_ratio, 4),
                    image_dpi_estimate=round(image_dpi, 1) if image_dpi else None,
                    scanned_likelihood=round(scanned_likelihood, 4),
                    ocr_present_likelihood=round(ocr_present, 4),
                    table_likelihood=round(table_likelihood, 4),
                    has_reliable_native_text=reliable_text,
                    needs_ocr=needs_ocr,
                    kind=kind,
                )
            )
        doc.close()

        kinds = {p.kind for p in page_results}
        if kinds == {"digital"}:
            document_kind = "digital"
        elif kinds == {"scanned"}:
            document_kind = "scanned"
        elif len(kinds) > 1 or "mixed" in kinds:
            document_kind = "mixed"
        else:
            document_kind = next(iter(kinds), "unknown")
        financial_score = financial_text_score("\n".join(all_text))
        return DocumentInspection(
            source_filename=path.name,
            sha256=sha256_file(path),
            page_count=len(page_results),
            encrypted=encrypted,
            metadata=metadata,
            pages=page_results,
            document_kind=document_kind,  # type: ignore[arg-type]
            financial_statement_likelihood=financial_score,
            appears_financial_statement=financial_score >= 0.42,
        )

    def extract_native_words(self, path: str | Path) -> dict[int, list[Word]]:
        doc = fitz.open(str(path))
        out = {idx + 1: self._native_words(page) for idx, page in enumerate(doc)}
        doc.close()
        return out
