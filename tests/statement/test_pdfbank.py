from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pdfbank import StatementExtractor
from pdfbank.inspector import DocumentInspector
from pdfbank.models import Balances, Transaction
from pdfbank.utils import parse_money
from pdfbank.validation import FinancialValidator

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,234.56", 1234.56),
        ("-$84.31", -84.31),
        ("($100.00)", -100.0),
        ("100.00 DR", -100.0),
        ("100.00 CR", 100.0),
        ("+$2,400.00", 2400.0),
        ("1.234,56", 1234.56),
        ("1 234,56", 1234.56),
    ],
)
def test_parse_money_variants(raw, expected):
    value, warnings = parse_money(raw)
    assert value == expected
    assert not warnings


def test_parse_money_rejects_malformed():
    value, warnings = parse_money("12..3")
    assert value is None
    assert warnings


def test_inspect_native_pdf():
    result = DocumentInspector().inspect(FIX / "native_standard.pdf")
    assert result.page_count == 1
    assert result.document_kind == "digital"
    assert result.pages[0].has_reliable_native_text
    assert not result.pages[0].needs_ocr
    assert result.pages[0].table_likelihood > 0.2


def test_inspect_scanned_pdf():
    result = DocumentInspector().inspect(FIX / "scanned_statement.pdf")
    assert result.document_kind == "scanned"
    assert result.pages[0].needs_ocr
    assert result.pages[0].image_area_ratio > 0.8


def test_inspect_mixed_pdf():
    result = DocumentInspector().inspect(FIX / "mixed_statement.pdf")
    assert result.page_count == 2
    assert result.document_kind == "mixed"
    assert result.pages[0].kind == "digital"
    assert result.pages[1].kind == "scanned"


def test_rotated_page_inspection():
    result = DocumentInspector().inspect(FIX / "rotated_scan.pdf")
    assert result.pages[0].rotation in {90, 270}
    assert result.pages[0].needs_ocr


def test_mixed_page_adaptive_processing():
    result = StatementExtractor().process(FIX / "mixed_statement.pdf")
    assert result.inspection.document_kind == "mixed"
    assert result.debug["ocr_pages"] == 1
    assert [p.selected_source for p in result.pages] == ["native_pdf_text", "tesseract_ocr"]
    assert len(result.transactions) == 4
    assert result.validation and result.validation.status == "validated"


def test_native_extraction_and_reconciliation():
    result = StatementExtractor().process(FIX / "native_standard.pdf")
    assert len(result.transactions) == 4
    assert result.balances.opening_balance == 1000.0
    assert result.balances.closing_balance == 3216.94
    assert result.validation and result.validation.status == "validated"
    assert result.debug["ocr_pages"] == 0
    assert all(t.bounding_box for t in result.transactions)
    assert all("amount" in t.field_provenance for t in result.transactions)


def test_scanned_uses_ocr_with_coordinates():
    result = StatementExtractor().process(FIX / "scanned_statement.pdf")
    assert result.debug["ocr_pages"] == 1
    assert len(result.transactions) == 4
    assert result.pages[0].selected_source == "tesseract_ocr"
    assert result.pages[0].ocr is not None
    assert result.pages[0].ocr.words
    assert all(word.bbox for word in result.pages[0].ocr.words)
    assert result.validation and result.validation.status == "validated"


def test_rotated_scan_extraction():
    result = StatementExtractor().process(FIX / "rotated_scan.pdf")
    assert len(result.transactions) == 4
    assert result.validation and result.validation.status == "validated"


def test_low_resolution_scan_fails_explicitly_not_fabricated():
    result = StatementExtractor().process(FIX / "lowres_scan.pdf")
    assert result.pages[0].ocr is not None
    assert result.pages[0].ocr.mean_confidence < 0.8
    # This intentionally severe fixture is expected to remain uncertain, not invent transactions.
    assert result.validation is not None
    assert result.validation.status in {"insufficient_data", "warnings", "failed"}


def test_debit_credit_columns():
    result = StatementExtractor().process(FIX / "debit_credit.pdf")
    assert [t.amount for t in result.transactions] == [-1200.0, 2500.0, -15.0]
    assert [t.debit_credit for t in result.transactions] == ["debit", "credit", "debit"]
    assert result.validation and result.validation.status == "validated"


def test_multiline_and_page_boundary_continuation():
    result = StatementExtractor().process(FIX / "multiline_multipage.pdf")
    assert len(result.transactions) == 3
    assert "REFERENCE 998877 SECOND LINE" in result.transactions[0].description
    assert "CONTINUED TRANSFER MEMO" in result.transactions[1].description
    assert result.validation and result.validation.status == "validated"


def test_reconciliation_failure_is_not_corrected():
    result = StatementExtractor().process(FIX / "reconciliation_failure.pdf")
    assert result.balances.closing_balance == 3999.99
    assert result.validation and result.validation.status == "failed"
    assert result.validation.reconciliation_difference is not None
    assert result.balances.closing_balance != 3216.94


def test_duplicate_detection():
    tx = Transaction(
        transaction_id="a", date="2025-06-01", description="DUP", amount=-10.0,
        balance_after_transaction=90.0, page_number=1, extraction_method="test"
    )
    tx2 = tx.model_copy(update={"transaction_id": "b"})
    validation = FinancialValidator().validate(Balances(opening_balance=100.0, closing_balance=80.0), [tx, tx2])
    assert set(validation.duplicate_transaction_ids) == {"a", "b"}
    assert any(i.code == "duplicate_transaction" for i in validation.issues)


def test_outputs_include_provenance(tmp_path):
    extractor = StatementExtractor()
    source = FIX / "native_standard.pdf"
    result = extractor.process(source)
    extractor.write_outputs(result, source, tmp_path)
    for name in ["transactions.csv", "statement.json", "validation.json", "extraction_report.html", "inspection.json"]:
        assert (tmp_path / name).exists()
    payload = json.loads((tmp_path / "statement.json").read_text())
    assert payload["provenance"]
    assert payload["transactions"][0]["field_provenance"]["amount"]["bbox"]
    rows = list(csv.DictReader((tmp_path / "transactions.csv").open()))
    assert rows[0]["source_file"] == source.name
    assert rows[0]["source_text"]
    assert rows[0]["extraction_method"]


def test_table_methods_available():
    native = StatementExtractor().process(FIX / "native_standard.pdf")
    scanned = StatementExtractor().process(FIX / "scanned_statement.pdf")
    assert native.tables
    assert scanned.tables
    assert any(t.extraction_method.startswith(("pdfplumber", "camelot", "native_geometry")) for t in native.tables)
    assert any(t.extraction_method in {"opencv_ruled_grid", "ocr_geometry"} for t in scanned.tables)


def test_api_process_directory(tmp_path):
    indir = tmp_path / "in"; outdir = tmp_path / "out"; indir.mkdir()
    # Avoid copying the OCR-heavy fixtures in this unit test.
    (indir / "a.pdf").write_bytes((FIX / "native_standard.pdf").read_bytes())
    (indir / "b.pdf").write_bytes((FIX / "debit_credit.pdf").read_bytes())
    results = StatementExtractor().process_directory(indir, outdir)
    assert len(results) == 2
    assert (outdir / "a" / "statement.json").exists()
    assert (outdir / "b" / "transactions.csv").exists()


def test_validator_detects_impossible_date_and_ambiguity():
    tx = Transaction(
        transaction_id="bad-date", date="2025-02-30", description="UNKNOWN ITEM", amount=10.0,
        debit_credit="unknown", balance_after_transaction=110.0, page_number=1, extraction_method="test"
    )
    validation = FinancialValidator().validate(Balances(opening_balance=100.0, closing_balance=110.0), [tx])
    codes = {issue.code for issue in validation.issues}
    assert "invalid_date" in codes
    assert "debit_credit_ambiguity" in codes
    assert validation.status == "warnings"


def test_validator_detects_malformed_amount_and_page_boundary_error():
    tx1 = Transaction(
        transaction_id="one", date="2025-06-01", description="A", amount=-10.0,
        debit_credit="debit", balance_after_transaction=90.0, page_number=1, extraction_method="test"
    )
    tx2 = Transaction(
        transaction_id="two", date="2025-06-02", description="B", amount=20.0,
        debit_credit="credit", balance_after_transaction=999.0, page_number=2, extraction_method="test"
    )
    tx3 = Transaction(
        transaction_id="three", date="2025-06-03", description="C", amount=None,
        debit_credit="unknown", balance_after_transaction=None, page_number=2, extraction_method="test"
    )
    validation = FinancialValidator().validate(Balances(opening_balance=100.0, closing_balance=999.0), [tx1, tx2, tx3])
    codes = {issue.code for issue in validation.issues}
    assert "page_boundary_balance_error" in codes
    assert "malformed_amount" in codes


def test_suspicious_ocr_substitution_detector():
    from pdfbank.models import NormalizedPage, Word
    from pdfbank.validation import detect_suspicious_ocr_tokens

    suspicious = Word(text="$1O0.00", bbox=(10, 10, 50, 20), confidence=0.61, source="tesseract_ocr")
    normal = Word(text="$100.00", bbox=(60, 10, 100, 20), confidence=0.99, source="tesseract_ocr")
    page = NormalizedPage(
        page_number=1, width_pt=612, height_pt=792, rotation=0, kind="scanned",
        selected_source="tesseract_ocr", words=[suspicious, normal], ocr_words=[suspicious, normal]
    )
    found = detect_suspicious_ocr_tokens([page])
    assert len(found) == 1
    assert found[0]["source_text"] == "$1O0.00"


def test_onnx_layout_decoder_contract_without_model_runtime():
    import numpy as np
    from pdfbank.layout import OnnxLayoutBackend

    backend = OnnxLayoutBackend.__new__(OnnxLayoutBackend)
    backend.labels = ["table", "text"]
    backend.output_format = "xyxy_score_class"
    backend.confidence_threshold = 0.35
    detections = backend._decode(np.array([[10, 20, 110, 220, 0.9, 0], [1, 2, 3, 4, 0.1, 1]], dtype=np.float32))
    assert len(detections) == 1
    assert detections[0][5] == 0


def test_locked_encrypted_pdf_is_identified_before_extraction(tmp_path):
    from pypdf import PdfReader, PdfWriter
    src = PdfReader(str(FIX / "native_standard.pdf"))
    writer = PdfWriter()
    for page in src.pages:
        writer.add_page(page)
    writer.encrypt("secret")
    encrypted = tmp_path / "locked.pdf"
    with encrypted.open("wb") as handle:
        writer.write(handle)
    inspection = DocumentInspector().inspect(encrypted)
    assert inspection.encrypted is True
    assert inspection.page_count == 1
    assert inspection.metadata.get("encryption_status") == "locked"
    with pytest.raises(PermissionError):
        StatementExtractor().process(encrypted)
