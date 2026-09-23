from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import time

from .confidence import apply_validation_confidence
from .inspector import DocumentInspector
from .layout import HeuristicLayoutBackend, OnnxLayoutBackend, normalize_page
from .models import StatementResult, ValidationIssue
from .ocr import OCRService, TesseractBackend
from .report import build_html_report
from .semantics import SemanticExtractor
from .tables import TableExtractor
from .transactions import TransactionParser
from .validation import FinancialValidator, detect_suspicious_ocr_tokens

LOG = logging.getLogger("pdfbank")


class StatementExtractor:
    def __init__(
        self,
        ocr: bool = True,
        tables: bool = True,
        validation: bool = True,
        ocr_engine: str = "tesseract",
        preprocessing: str = "adaptive",
        language: str = "eng",
        dpi: int = 300,
        reconciliation_tolerance: float = 0.01,
        preserve_debug: bool = True,
        layout_backend: str = "heuristic",
        layout_model: str | Path | None = None,
        layout_config: str | Path | None = None,
    ) -> None:
        self.enable_ocr = ocr
        self.enable_tables = tables
        self.enable_validation = validation
        self.ocr_engine = ocr_engine
        self.preprocessing = preprocessing
        self.inspector = DocumentInspector()
        self.ocr_service = OCRService(TesseractBackend(language=language, dpi=dpi))
        self.table_extractor = TableExtractor()
        self.semantic_extractor = SemanticExtractor()
        self.transaction_parser = TransactionParser()
        self.validator = FinancialValidator(tolerance=reconciliation_tolerance)
        if layout_backend == "heuristic":
            self.layout_backend = HeuristicLayoutBackend()
        elif layout_backend == "onnx":
            model_root = Path(os.environ.get("PDFBANK_MODEL_DIR", "/models/pdfbank"))
            model_path = Path(layout_model) if layout_model else model_root / "layout" / "model.onnx"
            config_path = Path(layout_config) if layout_config else model_root / "layout" / "config.json"
            self.layout_backend = OnnxLayoutBackend(model_path, config_path)
        else:
            raise ValueError(f"Unsupported layout backend: {layout_backend}")
        self.preserve_debug = preserve_debug

    def process(self, pdf_path: str | Path, force_ocr: bool = False) -> StatementResult:
        started = time.perf_counter()
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected PDF input: {pdf_path}")

        # PASS 1: structural analysis.
        inspection = self.inspector.inspect(pdf_path)
        if inspection.encrypted and not inspection.pages:
            raise PermissionError("PDF is encrypted and locked; extraction requires an explicitly decrypted working copy")
        native = self.inspector.extract_native_words(pdf_path)

        # PASS 2/3: native words are already extracted; OCR only page-wise where indicated.
        pages = []
        ocr_pages = 0
        for page_info in inspection.pages:
            ocr_result = None
            needs = force_ocr or page_info.needs_ocr
            if self.enable_ocr and needs:
                LOG.info("OCR page %s (%s)", page_info.page_number, page_info.kind)
                try:
                    ocr_result = self.ocr_service.ocr(
                        pdf_path,
                        page_info.page_number,
                        engine=self.ocr_engine,
                        preprocessing=self.preprocessing,
                    )
                    ocr_pages += 1
                except Exception as exc:
                    LOG.warning("OCR failed on page %s: %s", page_info.page_number, type(exc).__name__)
            page = normalize_page(
                page_number=page_info.page_number,
                width_pt=page_info.width_pt,
                height_pt=page_info.height_pt,
                rotation=page_info.rotation,
                kind=page_info.kind,
                native_words=native.get(page_info.page_number, []),
                ocr_words=ocr_result.words if ocr_result else [],
                ocr_result=ocr_result,
            )
            pages.append(page)

        # PASS 4: deterministic layout labels, retained in debug view.
        layout_regions = {str(p.page_number): self.layout_backend.analyze(pdf_path, p) for p in pages}

        # PASS 5: table extraction.
        extracted_tables = []
        if self.enable_tables:
            for page, page_info in zip(pages, inspection.pages):
                extracted_tables.extend(self.table_extractor.extract(pdf_path, page, page_info.table_likelihood))

        # PASS 7: semantic statement-level fields.
        semantic = self.semantic_extractor.extract(pdf_path, pages, inspection.sha256)

        # PASS 6/7: transaction candidates and semantic row parsing.
        transactions = self.transaction_parser.parse(
            pages,
            statement_start=semantic.document.statement_start_date,
            statement_end=semantic.document.statement_end_date,
            currency=semantic.account.currency,
        )

        ocr_numeric_suspects = detect_suspicious_ocr_tokens(pages)

        result = StatementResult(
            document=semantic.document,
            account=semantic.account,
            balances=semantic.balances,
            inspection=inspection,
            pages=pages,
            tables=extracted_tables,
            transactions=transactions,
            provenance=semantic.provenance,
            debug={"layout_regions": layout_regions} if self.preserve_debug else {},
        )

        # PASS 8/9: validation and confidence.
        if self.enable_validation:
            result.validation = self.validator.validate(result.balances, result.transactions)
            if ocr_numeric_suspects:
                seen_pages = sorted({item["page"] for item in ocr_numeric_suspects})
                for page_no in seen_pages:
                    result.validation.issues.append(ValidationIssue(
                        code="suspicious_ocr_substitution", severity="warning",
                        message="OCR contains digit/letter-confusable tokens; source review is recommended",
                        page=page_no,
                    ))
                if result.validation.status == "validated":
                    result.validation.status = "warnings"
            apply_validation_confidence(result)
        for txn in result.transactions:
            result.provenance.extend(txn.field_provenance.values())

        result.debug.update(
            {
                "processing_seconds": round(time.perf_counter() - started, 4),
                "ocr_pages": ocr_pages,
                "selected_sources": {str(p.page_number): p.selected_source for p in pages},
                "table_methods": [t.extraction_method for t in extracted_tables],
                "layout_backend": self.layout_backend.name,
                "ocr_numeric_suspects": ocr_numeric_suspects,
            }
        )
        return result

    def write_outputs(self, result: StatementResult, source_pdf: str | Path, output_dir: str | Path) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result.to_json(output_dir / "statement.json")
        result.to_csv(output_dir / "transactions.csv")
        (output_dir / "validation.json").write_text(
            result.validation.model_dump_json(indent=2) if result.validation else json.dumps({"status": "not_run"}, indent=2),
            encoding="utf-8",
        )
        (output_dir / "inspection.json").write_text(result.inspection.model_dump_json(indent=2), encoding="utf-8")
        (output_dir / "extraction_report.html").write_text(build_html_report(result, source_pdf), encoding="utf-8")
        return output_dir

    def process_directory(self, input_dir: str | Path, output_dir: str | Path) -> list[StatementResult]:
        input_dir, output_dir = Path(input_dir), Path(output_dir)
        results: list[StatementResult] = []
        for pdf in sorted(input_dir.glob("*.pdf")):
            result = self.process(pdf)
            self.write_outputs(result, pdf, output_dir / pdf.stem)
            results.append(result)
        return results

    def create_searchable_copy(self, pdf_path: str | Path, output_pdf: str | Path, language: str = "eng") -> None:
        """Optional non-destructive OCRmyPDF searchable derivative; original is untouched."""
        import subprocess

        cmd = [
            "ocrmypdf", "--skip-text", "--rotate-pages", "--deskew", "--optimize", "1",
            "-l", language, str(pdf_path), str(output_pdf),
        ]
        subprocess.run(cmd, check=True)
