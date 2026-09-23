from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict

BBox = tuple[float, float, float, float]


class ProvenanceRecord(BaseModel):
    field: str
    value: Any | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = None
    bbox: BBox | None = None
    source_text: str | None = None
    method: str
    validation_status: str = "unvalidated"
    warnings: list[str] = Field(default_factory=list)


class Word(BaseModel):
    text: str
    bbox: BBox
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: Literal["native_pdf_text", "tesseract_ocr"]
    block_id: int | None = None
    line_id: int | None = None


class TextLine(BaseModel):
    text: str
    bbox: BBox
    words: list[Word] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PageInspection(BaseModel):
    page_number: int
    width_pt: float
    height_pt: float
    rotation: int
    native_char_count: int
    text_density: float
    image_count: int
    image_area_ratio: float
    image_dpi_estimate: float | None = None
    scanned_likelihood: float
    ocr_present_likelihood: float
    table_likelihood: float
    has_reliable_native_text: bool
    needs_ocr: bool
    kind: Literal["digital", "scanned", "mixed", "unknown"]


class DocumentInspection(BaseModel):
    source_filename: str
    sha256: str
    page_count: int
    encrypted: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    pages: list[PageInspection]
    document_kind: Literal["digital", "scanned", "mixed", "unknown"]
    financial_statement_likelihood: float
    appears_financial_statement: bool


class OCRCandidateMetric(BaseModel):
    name: str
    mean_confidence: float
    token_count: int
    alnum_ratio: float
    financial_token_ratio: float
    score: float


class OCRPageResult(BaseModel):
    page_number: int
    backend: str = "tesseract"
    preprocessing: str
    words: list[Word] = Field(default_factory=list)
    text: str = ""
    mean_confidence: float = 0.0
    candidates: list[OCRCandidateMetric] = Field(default_factory=list)


class NormalizedPage(BaseModel):
    page_number: int
    width_pt: float
    height_pt: float
    rotation: int
    kind: str
    selected_source: str
    native_words: list[Word] = Field(default_factory=list)
    ocr_words: list[Word] = Field(default_factory=list)
    words: list[Word] = Field(default_factory=list)
    lines: list[TextLine] = Field(default_factory=list)
    text: str = ""
    ocr: OCRPageResult | None = None


class TableCell(BaseModel):
    row: int
    column: int
    text: str
    bbox: BBox | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedTable(BaseModel):
    page_number: int
    rows: list[list[str]] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    bbox: BBox | None = None
    source_text: str = ""
    extraction_method: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class DocumentData(BaseModel):
    document_id: str
    source_filename: str
    page_count: int
    institution: str | None = None
    document_type: str | None = None
    statement_start_date: str | None = None
    statement_end_date: str | None = None
    extraction_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AccountData(BaseModel):
    account_holder: str | None = None
    account_number: str | None = None
    masked_account_number: str | None = None
    account_type: str | None = None
    institution: str | None = None
    branch: str | None = None
    routing_information: str | None = None
    currency: str | None = None


class Balances(BaseModel):
    opening_balance: float | None = None
    closing_balance: float | None = None
    available_balance: float | None = None
    ledger_balance: float | None = None
    total_deposits: float | None = None
    total_withdrawals: float | None = None
    total_fees: float | None = None
    interest: float | None = None
    other_adjustments: float | None = None


class Transaction(BaseModel):
    transaction_id: str
    date: str | None = None
    posting_date: str | None = None
    description: str | None = None
    reference: str | None = None
    amount: float | None = None
    debit_credit: Literal["debit", "credit", "unknown"] = "unknown"
    transaction_type: str | None = None
    balance_after_transaction: float | None = None
    currency: str | None = None
    page_number: int
    bounding_box: BBox | None = None
    source_text: str = ""
    extraction_method: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_status: str = "unvalidated"
    warnings: list[str] = Field(default_factory=list)
    field_provenance: dict[str, ProvenanceRecord] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    transaction_id: str | None = None
    page: int | None = None


class ValidationResult(BaseModel):
    status: Literal["validated", "warnings", "failed", "insufficient_data"]
    reconciliation_difference: float | None = None
    reconciliation_tolerance: float = 0.01
    duplicate_transaction_ids: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class StatementResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    document: DocumentData
    account: AccountData = Field(default_factory=AccountData)
    balances: Balances = Field(default_factory=Balances)
    inspection: DocumentInspection
    pages: list[NormalizedPage] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    transactions: list[Transaction] = Field(default_factory=list)
    validation: ValidationResult | None = None
    provenance: list[ProvenanceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\f\n".join(page.text for page in self.pages)

    @property
    def ocr(self) -> list[OCRPageResult]:
        return [p.ocr for p in self.pages if p.ocr is not None]

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    def to_csv(self, path: str | Path) -> None:
        import csv

        fields = [
            "transaction_id", "date", "posting_date", "description", "reference",
            "amount", "debit_credit", "transaction_type", "balance_after_transaction",
            "currency", "source_file", "page", "bbox", "source_text", "confidence",
            "extraction_method", "validation_status", "warnings",
        ]
        with Path(path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for txn in self.transactions:
                writer.writerow({
                    "transaction_id": txn.transaction_id,
                    "date": txn.date,
                    "posting_date": txn.posting_date,
                    "description": txn.description,
                    "reference": txn.reference,
                    "amount": txn.amount,
                    "debit_credit": txn.debit_credit,
                    "transaction_type": txn.transaction_type,
                    "balance_after_transaction": txn.balance_after_transaction,
                    "currency": txn.currency,
                    "source_file": self.document.source_filename,
                    "page": txn.page_number,
                    "bbox": list(txn.bounding_box) if txn.bounding_box else None,
                    "source_text": txn.source_text,
                    "confidence": txn.confidence,
                    "extraction_method": txn.extraction_method,
                    "validation_status": txn.validation_status,
                    "warnings": " | ".join(txn.warnings),
                })
