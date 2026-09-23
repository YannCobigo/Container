from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import re
from typing import Iterable

from dateutil import parser as dateparser

from .models import NormalizedPage, ProvenanceRecord, TextLine, Transaction
from .utils import DATE_RE, MONEY_RE, parse_money, union_bbox


DATE_START_RE = re.compile(r"^\s*(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b")
SKIP_TERMS = [
    "opening balance", "closing balance", "ending balance", "total deposits", "total withdrawals",
    "total fees", "statement period", "account number", "page ", "daily balance", "summary",
]


@dataclass
class ColumnHints:
    date_x: float | None = None
    description_x: float | None = None
    debit_x: float | None = None
    credit_x: float | None = None
    amount_x: float | None = None
    balance_x: float | None = None
    header_y: float | None = None


def _normalize_date(raw: str, year_hint: int | None = None) -> str | None:
    try:
        default = date(year_hint or 2000, 1, 1)
        dt = dateparser.parse(raw, default=dateparser.parse(default.isoformat()), dayfirst=False, fuzzy=False)
        if dt is None:
            return None
        # When source omits year, use explicit statement year if available.
        if year_hint and len(re.findall(r"\d+", raw)) == 2:
            dt = dt.replace(year=year_hint)
        if dt.year < 1990 or dt.year > 2100:
            return None
        return dt.date().isoformat()
    except Exception:
        return None


def _find_header_hints(page: NormalizedPage) -> ColumnHints:
    hints = ColumnHints()
    for line in page.lines:
        low = line.text.lower()
        if "date" not in low or not any(k in low for k in ["amount", "debit", "credit", "balance", "description"]):
            continue
        hints.header_y = line.bbox[3]
        for word in line.words:
            token = re.sub(r"[^a-z]", "", word.text.lower())
            cx = (word.bbox[0] + word.bbox[2]) / 2
            if token.startswith("date"):
                hints.date_x = cx
            elif token.startswith("desc") or token in {"details", "transaction"}:
                hints.description_x = cx
            elif token.startswith("debit") or token in {"withdrawal", "withdrawals"}:
                hints.debit_x = cx
            elif token.startswith("credit") or token in {"deposit", "deposits"}:
                hints.credit_x = cx
            elif token.startswith("amount"):
                hints.amount_x = cx
            elif token.startswith("balance"):
                hints.balance_x = cx
        return hints
    return hints


def _money_tokens(line: TextLine) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    out = []
    for match in MONEY_RE.finditer(line.text):
        raw = match.group(0)
        # derive x from word overlap with raw token when possible; fallback sequential geometry.
        digits = re.sub(r"\D", "", raw)
        candidates = [w for w in line.words if digits and re.sub(r"\D", "", w.text) and re.sub(r"\D", "", w.text) in digits]
        if candidates:
            bbox = union_bbox(w.bbox for w in candidates)
            x = (bbox[0] + bbox[2]) / 2 if bbox else line.bbox[2]
        else:
            bbox = line.bbox
            x = line.bbox[2]
        out.append((raw, x, bbox))
    return out


def _description_from_line(line: TextLine, date_raw: str, money_raws: list[str]) -> str:
    text = line.text
    pos = text.find(date_raw)
    if pos >= 0:
        text = text[pos + len(date_raw):]
    for raw in money_raws:
        text = text.replace(raw, " ")
    text = re.sub(r"\s+", " ", text).strip(" -|\t")
    return text or ""


class TransactionParser:
    def __init__(self, min_confidence: float = 0.35) -> None:
        self.min_confidence = min_confidence

    def parse(self, pages: list[NormalizedPage], statement_start: str | None = None, statement_end: str | None = None, currency: str | None = None) -> list[Transaction]:
        year_hint = None
        for candidate in [statement_end, statement_start]:
            if candidate:
                try:
                    year_hint = int(candidate[:4])
                    break
                except Exception:
                    pass

        transactions: list[Transaction] = []
        pending: Transaction | None = None
        for page in pages:
            hints = _find_header_hints(page)
            for line in page.lines:
                text = line.text.strip()
                low = text.lower()
                if not text:
                    continue
                if hints.header_y is not None and line.bbox[3] <= hints.header_y + 2:
                    # Ignore page heading and the transaction-table header itself.
                    continue
                if any(term in low for term in SKIP_TERMS):
                    continue

                dm = DATE_START_RE.match(text)
                money = _money_tokens(line)
                if dm and money:
                    if pending is not None:
                        transactions.append(pending)
                        pending = None
                    raw_date = dm.group(1)
                    normalized_date = _normalize_date(raw_date, year_hint)
                    money_raws = [m[0] for m in money]
                    desc = _description_from_line(line, raw_date, money_raws)

                    parsed = [(raw, parse_money(raw)[0], x, bbox) for raw, x, bbox in money]
                    parsed = [p for p in parsed if p[1] is not None]
                    if not parsed:
                        continue

                    amount = None
                    balance = None
                    debit_credit = "unknown"
                    amount_raw = None
                    amount_bbox = None
                    balance_raw = None
                    balance_bbox = None

                    # Column-aware mapping first.
                    if hints.debit_x is not None or hints.credit_x is not None:
                        for raw, value, x, bbox in parsed:
                            choices = []
                            if hints.debit_x is not None:
                                choices.append((abs(x - hints.debit_x), "debit"))
                            if hints.credit_x is not None:
                                choices.append((abs(x - hints.credit_x), "credit"))
                            if hints.balance_x is not None:
                                choices.append((abs(x - hints.balance_x), "balance"))
                            target = min(choices, default=(9999, "unknown"))[1]
                            if target == "balance":
                                balance, balance_raw, balance_bbox = value, raw, bbox
                            elif target == "debit" and amount is None:
                                amount, amount_raw, amount_bbox, debit_credit = -abs(value), raw, bbox, "debit"
                            elif target == "credit" and amount is None:
                                amount, amount_raw, amount_bbox, debit_credit = abs(value), raw, bbox, "credit"
                    elif hints.amount_x is not None:
                        amount_item = min(parsed, key=lambda p: abs(p[2] - hints.amount_x))
                        amount, amount_raw, amount_bbox = amount_item[1], amount_item[0], amount_item[3]
                        if hints.balance_x is not None and len(parsed) > 1:
                            balance_item = min(parsed, key=lambda p: abs(p[2] - hints.balance_x))
                            if balance_item != amount_item:
                                balance, balance_raw, balance_bbox = balance_item[1], balance_item[0], balance_item[3]
                    else:
                        # Generic bank layout: if two terminal money values, last is balance.
                        if len(parsed) >= 2:
                            amount, amount_raw, amount_bbox = parsed[-2][1], parsed[-2][0], parsed[-2][3]
                            balance, balance_raw, balance_bbox = parsed[-1][1], parsed[-1][0], parsed[-1][3]
                        else:
                            amount, amount_raw, amount_bbox = parsed[-1][1], parsed[-1][0], parsed[-1][3]

                    if amount is None:
                        # If mapping failed, choose the first non-balance token conservatively.
                        amount, amount_raw, amount_bbox = parsed[0][1], parsed[0][0], parsed[0][3]

                    if debit_credit == "unknown":
                        raw_upper = (amount_raw or "").upper()
                        if amount is not None and amount < 0:
                            debit_credit = "debit"
                        elif "DR" in raw_upper or raw_upper.strip().startswith("("):
                            debit_credit = "debit"
                            amount = -abs(amount)
                        elif "CR" in raw_upper or raw_upper.strip().startswith("+"):
                            debit_credit = "credit"
                            amount = abs(amount)
                        else:
                            # Lexical sign evidence; leave unknown when absent.
                            if re.search(r"\b(?:deposit|credit|payment received|interest)\b", desc, re.I):
                                debit_credit = "credit"
                                amount = abs(amount)
                            elif re.search(r"\b(?:withdrawal|debit|fee|purchase|check|atm|payment)\b", desc, re.I):
                                debit_credit = "debit"
                                amount = -abs(amount)

                    confidence = line.confidence
                    warnings: list[str] = []
                    if normalized_date is None:
                        confidence *= 0.65
                        warnings.append("invalid_or_ambiguous_date")
                    if debit_credit == "unknown" and amount is not None and amount >= 0:
                        confidence *= 0.78
                        warnings.append("debit_credit_ambiguous")
                    if not desc:
                        confidence *= 0.8
                        warnings.append("missing_description")
                    method = page.selected_source
                    tx_seed = f"{page.page_number}|{line.bbox}|{normalized_date}|{desc}|{amount}|{balance}"
                    txid = hashlib.sha256(tx_seed.encode()).hexdigest()[:20]
                    txn = Transaction(
                        transaction_id=txid,
                        date=normalized_date,
                        description=desc or None,
                        amount=round(amount, 2) if amount is not None else None,
                        debit_credit=debit_credit,  # type: ignore[arg-type]
                        balance_after_transaction=round(balance, 2) if balance is not None else None,
                        currency=currency,
                        page_number=page.page_number,
                        bounding_box=line.bbox,
                        source_text=line.text,
                        extraction_method=method,
                        confidence=max(self.min_confidence, min(1.0, confidence)),
                        warnings=warnings,
                    )
                    txn.field_provenance["date"] = ProvenanceRecord(
                        field=f"transactions.{txid}.date", value=normalized_date, confidence=txn.confidence,
                        page=page.page_number, bbox=line.bbox, source_text=raw_date, method=method,
                        validation_status="unvalidated", warnings=["invalid_or_ambiguous_date"] if normalized_date is None else [],
                    )
                    txn.field_provenance["amount"] = ProvenanceRecord(
                        field=f"transactions.{txid}.amount", value=txn.amount, confidence=txn.confidence,
                        page=page.page_number, bbox=amount_bbox, source_text=amount_raw, method=method,
                        validation_status="unvalidated", warnings=["debit_credit_ambiguous"] if debit_credit == "unknown" else [],
                    )
                    if balance is not None:
                        txn.field_provenance["balance_after_transaction"] = ProvenanceRecord(
                            field=f"transactions.{txid}.balance_after_transaction", value=txn.balance_after_transaction,
                            confidence=txn.confidence, page=page.page_number, bbox=balance_bbox,
                            source_text=balance_raw, method=method, validation_status="unvalidated",
                        )
                    txn.field_provenance["description"] = ProvenanceRecord(
                        field=f"transactions.{txid}.description", value=txn.description, confidence=txn.confidence,
                        page=page.page_number, bbox=line.bbox, source_text=line.text, method=method,
                        validation_status="unvalidated",
                    )
                    pending = txn
                    continue

                # Continuation rows: no leading date; must be text-heavy, not summary/header.
                if pending is not None and not dm and not money and len(text) >= 3:
                    if not any(term in low for term in SKIP_TERMS) and not re.fullmatch(r"[\W\d]+", text):
                        pending.description = ((pending.description or "") + " " + text).strip()
                        pending.source_text = (pending.source_text + "\n" + text).strip()
                        pending.bounding_box = union_bbox([pending.bounding_box, line.bbox] if pending.bounding_box else [line.bbox])
                        pending.confidence = min(pending.confidence, line.confidence)
                        pending.field_provenance["description"] = ProvenanceRecord(
                            field=f"transactions.{pending.transaction_id}.description",
                            value=pending.description,
                            confidence=pending.confidence,
                            page=pending.page_number,
                            bbox=pending.bounding_box,
                            source_text=pending.source_text,
                            method=pending.extraction_method,
                            validation_status="unvalidated",
                        )
            # Keep pending across page boundaries so wrapped descriptions can continue on next page.
        if pending is not None:
            transactions.append(pending)
        return transactions
