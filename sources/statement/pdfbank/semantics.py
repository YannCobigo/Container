from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from dateutil import parser as dateparser

from .models import AccountData, Balances, DocumentData, NormalizedPage, ProvenanceRecord
from .utils import MONEY_RE, parse_money, union_bbox


@dataclass
class SemanticExtraction:
    document: DocumentData
    account: AccountData
    balances: Balances
    provenance: list[ProvenanceRecord]


INSTITUTION_PATTERNS = [
    ("JPMorgan Chase", re.compile(r"\b(?:jpmorgan\s+chase|chase)\b", re.I)),
    ("Bank of America", re.compile(r"\bbank\s+of\s+america\b", re.I)),
    ("Wells Fargo", re.compile(r"\bwells\s+fargo\b", re.I)),
    ("Citibank", re.compile(r"\b(?:citibank|citi)\b", re.I)),
    ("Capital One", re.compile(r"\bcapital\s+one\b", re.I)),
    ("U.S. Bank", re.compile(r"\bu\.?s\.?\s+bank\b", re.I)),
]


def _prov(field: str, value, line, method: str, confidence: float, validation: str = "unvalidated", warnings=None):
    return ProvenanceRecord(
        field=field,
        value=value,
        confidence=confidence,
        page=line[0] if line else None,
        bbox=line[1].bbox if line else None,
        source_text=line[1].text if line else None,
        method=method,
        validation_status=validation,
        warnings=warnings or [],
    )


def _find_labeled_amount(lines: list[tuple[int, object]], labels: list[str]) -> tuple[float | None, tuple[int, object] | None, str | None]:
    for page_no, line in lines:
        lower = line.text.lower()
        if any(label in lower for label in labels):
            matches = list(MONEY_RE.finditer(line.text))
            if matches:
                raw = matches[-1].group(0)
                value, warnings = parse_money(raw)
                if value is not None:
                    return value, (page_no, line), raw
    return None, None, None


def _normalize_date(raw: str) -> str | None:
    try:
        dt = dateparser.parse(raw, dayfirst=False, fuzzy=False)
        return dt.date().isoformat()
    except Exception:
        return None


class SemanticExtractor:
    def extract(self, source_path: str | Path, pages: list[NormalizedPage], sha256: str) -> SemanticExtraction:
        source_path = Path(source_path)
        all_lines = [(page.page_number, line) for page in pages for line in page.lines if line.text.strip()]
        text = "\n".join(line.text for _, line in all_lines)
        provenance: list[ProvenanceRecord] = []

        institution = None
        institution_line = None
        for name, pattern in INSTITUTION_PATTERNS:
            for item in all_lines:
                if pattern.search(item[1].text):
                    institution, institution_line = name, item
                    break
            if institution:
                break
        if institution:
            provenance.append(_prov("document.institution", institution, institution_line, "lexical_institution_match", 0.90))

        # Statement period supports range text and separately labeled begin/end dates.
        start_date = end_date = None
        period_line = None
        period_patterns = [
            re.compile(r"statement\s+(?:period|dates?)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:-|to|through|–|—)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I),
            re.compile(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:-|to|through|–|—)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I),
        ]
        for item in all_lines:
            for pattern in period_patterns:
                m = pattern.search(item[1].text)
                if m:
                    start_date, end_date = _normalize_date(m.group(1)), _normalize_date(m.group(2))
                    period_line = item
                    break
            if period_line:
                break
        if start_date:
            provenance.append(_prov("document.statement_start_date", start_date, period_line, "labeled_period_regex", 0.93))
        if end_date:
            provenance.append(_prov("document.statement_end_date", end_date, period_line, "labeled_period_regex", 0.93))

        document = DocumentData(
            document_id=sha256[:20],
            source_filename=source_path.name,
            page_count=len(pages),
            institution=institution,
            document_type="bank_statement" if any("statement" in line.text.lower() for _, line in all_lines) else None,
            statement_start_date=start_date,
            statement_end_date=end_date,
        )

        account = AccountData(institution=institution)
        # Account holder: only extract explicitly labeled holder/customer/name lines.
        holder_patterns = [
            re.compile(r"(?:account\s+holder|customer\s+name|statement\s+for)\s*[:\-]\s*(.+)$", re.I),
        ]
        for item in all_lines:
            for pattern in holder_patterns:
                m = pattern.search(item[1].text)
                if m and 2 <= len(m.group(1).strip()) <= 100:
                    account.account_holder = m.group(1).strip()
                    provenance.append(_prov("account.account_holder", account.account_holder, item, "labeled_field_regex", 0.88))
                    break
            if account.account_holder:
                break

        acct_pattern = re.compile(r"(?:account\s*(?:number|no\.?|#))\s*[:\-]?\s*([*xX•\-\s\d]{4,})", re.I)
        for item in all_lines:
            m = acct_pattern.search(item[1].text)
            if not m:
                continue
            raw = m.group(1).strip()
            digits = re.sub(r"\D", "", raw)
            if len(digits) < 4:
                continue
            masked = "***" + digits[-4:]
            if any(ch in raw for ch in "*xX•") or len(digits) <= 4:
                account.masked_account_number = masked
            else:
                account.account_number = digits
                account.masked_account_number = masked
            provenance.append(_prov("account.masked_account_number", masked, item, "labeled_account_regex", 0.96))
            if account.account_number:
                provenance.append(_prov("account.account_number", account.account_number, item, "labeled_account_regex", 0.96))
            break

        routing_pattern = re.compile(r"(?:routing|aba)\s*(?:number|no\.?|#)?\s*[:\-]?\s*(\d{9})\b", re.I)
        for item in all_lines:
            m = routing_pattern.search(item[1].text)
            if m:
                account.routing_information = m.group(1)
                provenance.append(_prov("account.routing_information", account.routing_information, item, "labeled_routing_regex", 0.96))
                break

        account_type_terms = ["checking", "savings", "money market", "credit card"]
        for item in all_lines:
            low = item[1].text.lower()
            hit = next((term for term in account_type_terms if term in low), None)
            if hit:
                account.account_type = hit.title()
                provenance.append(_prov("account.account_type", account.account_type, item, "lexical_account_type", 0.80))
                break

        currency = None
        if "$" in text:
            currency = "USD"
        elif "€" in text:
            currency = "EUR"
        elif "£" in text:
            currency = "GBP"
        if currency:
            account.currency = currency
            provenance.append(ProvenanceRecord(field="account.currency", value=currency, confidence=0.82, method="currency_symbol", validation_status="unvalidated"))

        labels = {
            "opening_balance": ["opening balance", "beginning balance", "starting balance"],
            "closing_balance": ["closing balance", "ending balance", "new balance"],
            "available_balance": ["available balance"],
            "ledger_balance": ["ledger balance"],
            "total_deposits": ["total deposits", "deposits and additions", "credits total"],
            "total_withdrawals": ["total withdrawals", "withdrawals and subtractions", "debits total"],
            "total_fees": ["total fees", "fees charged"],
            "interest": ["interest paid", "interest earned", "total interest"],
            "other_adjustments": ["other adjustments", "adjustments"],
        }
        values = {}
        for field, field_labels in labels.items():
            value, item, raw = _find_labeled_amount(all_lines, field_labels)
            values[field] = value
            if value is not None:
                provenance.append(_prov(f"balances.{field}", value, item, "labeled_amount_regex", 0.92))
        balances = Balances(**values)

        return SemanticExtraction(document=document, account=account, balances=balances, provenance=provenance)
