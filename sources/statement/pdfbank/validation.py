from __future__ import annotations

from collections import Counter
from datetime import date as Date
from decimal import Decimal
import re

from .models import Balances, NormalizedPage, Transaction, ValidationIssue, ValidationResult



def detect_suspicious_ocr_tokens(pages: list[NormalizedPage]) -> list[dict]:
    """Return OCR tokens containing digit/letter confusions without correcting them."""
    confusable = re.compile(r"(?i)(?=.*\d)(?=.*[OIlS])[\dOIlS$€£¥.,/() +\-]{3,}")
    suspects: list[dict] = []
    for page in pages:
        if not page.selected_source.startswith("tesseract"):
            continue
        for word in page.words:
            token = word.text.strip()
            if confusable.fullmatch(token):
                suspects.append({
                    "page": page.page_number,
                    "bbox": list(word.bbox),
                    "source_text": token,
                    "confidence": word.confidence,
                })
    return suspects


def D(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


class FinancialValidator:
    def __init__(self, tolerance: float = 0.01) -> None:
        self.tolerance = Decimal(str(tolerance))

    def validate(self, balances: Balances, transactions: list[Transaction]) -> ValidationResult:
        issues: list[ValidationIssue] = []
        duplicate_ids: list[str] = []

        # Duplicate detection uses semantic tuple, not generated transaction_id.
        keys = [
            (t.date, (t.description or "").strip().lower(), t.amount, t.balance_after_transaction)
            for t in transactions
        ]
        counts = Counter(keys)
        for key, count in counts.items():
            if count > 1:
                for t in transactions:
                    if (t.date, (t.description or "").strip().lower(), t.amount, t.balance_after_transaction) == key:
                        duplicate_ids.append(t.transaction_id)
                        t.warnings.append("possible_duplicate_transaction")
                        t.validation_status = "warning"
                issues.append(ValidationIssue(code="duplicate_transaction", severity="warning", message=f"Possible duplicate transaction occurs {count} times"))

        # Individual balance continuity when running balances are present.
        previous_balance = D(balances.opening_balance)
        previous_page: int | None = None
        continuity_checked = 0
        continuity_failed = 0
        for t in transactions:
            amount = D(t.amount)
            current = D(t.balance_after_transaction)
            date_invalid = t.date is None
            if t.date is not None:
                try:
                    Date.fromisoformat(t.date)
                except ValueError:
                    date_invalid = True
            if date_invalid:
                issues.append(ValidationIssue(code="invalid_date", severity="warning", message="Transaction date is missing or impossible", transaction_id=t.transaction_id, page=t.page_number))
            if t.debit_credit == "unknown" and amount is not None:
                issues.append(ValidationIssue(code="debit_credit_ambiguity", severity="warning", message="Transaction sign lacks reliable debit/credit evidence", transaction_id=t.transaction_id, page=t.page_number))
            if amount is None:
                issues.append(ValidationIssue(code="malformed_amount", severity="error", message="Transaction amount is missing or malformed", transaction_id=t.transaction_id, page=t.page_number))
            if previous_balance is not None and amount is not None and current is not None:
                continuity_checked += 1
                diff = (previous_balance + amount - current).copy_abs()
                if diff > self.tolerance:
                    continuity_failed += 1
                    code = "page_boundary_balance_error" if previous_page is not None and t.page_number != previous_page else "running_balance_mismatch"
                    t.warnings.append(f"{code}:{float(diff):.2f}")
                    t.validation_status = "reconciliation_failed"
                    issues.append(ValidationIssue(code=code, severity="error", message=f"Running balance differs by {float(diff):.2f}", transaction_id=t.transaction_id, page=t.page_number))
                else:
                    if t.validation_status == "unvalidated":
                        t.validation_status = "validated"
            if current is not None:
                previous_balance = current
            previous_page = t.page_number

        opening = D(balances.opening_balance)
        closing = D(balances.closing_balance)
        known_amounts = [D(t.amount) for t in transactions if t.amount is not None]
        reconciliation_difference = None
        overall_status = "insufficient_data"
        if opening is not None and closing is not None and len(known_amounts) == len(transactions) and transactions:
            computed = opening + sum(known_amounts, Decimal("0"))
            difference = computed - closing
            reconciliation_difference = float(difference)
            if difference.copy_abs() <= self.tolerance:
                overall_status = "validated" if not issues else "warnings"
            else:
                overall_status = "failed"
                issues.append(ValidationIssue(code="statement_reconciliation_failed", severity="error", message=f"Opening balance plus signed transactions differs from closing balance by {float(difference):.2f}"))
                for t in transactions:
                    if t.validation_status == "unvalidated":
                        t.validation_status = "statement_reconciliation_failed"
        elif issues:
            overall_status = "warnings"

        # Aggregate summary cross-checks when labels are available.
        credits = sum((D(t.amount) or Decimal("0")) for t in transactions if (t.amount or 0) > 0)
        debits = -sum((D(t.amount) or Decimal("0")) for t in transactions if (t.amount or 0) < 0)
        if balances.total_deposits is not None and (credits - D(balances.total_deposits)).copy_abs() > self.tolerance:
            issues.append(ValidationIssue(code="deposit_total_mismatch", severity="warning", message="Extracted transaction credits do not match stated total deposits"))
        if balances.total_withdrawals is not None and (debits - abs(D(balances.total_withdrawals))).copy_abs() > self.tolerance:
            issues.append(ValidationIssue(code="withdrawal_total_mismatch", severity="warning", message="Extracted transaction debits do not match stated total withdrawals"))

        # Ordering check only when normalized dates exist.
        dated = [(i, t.date) for i, t in enumerate(transactions) if t.date]
        if len(dated) >= 2:
            dates = [d for _, d in dated]
            inversions = sum(a > b for a, b in zip(dates, dates[1:]))
            if inversions:
                issues.append(ValidationIssue(code="transaction_ordering", severity="warning", message=f"Detected {inversions} date-order inversions"))

        return ValidationResult(
            status=overall_status,  # type: ignore[arg-type]
            reconciliation_difference=round(reconciliation_difference, 4) if reconciliation_difference is not None else None,
            reconciliation_tolerance=float(self.tolerance),
            duplicate_transaction_ids=sorted(set(duplicate_ids)),
            issues=issues,
            metrics={
                "transaction_count": len(transactions),
                "continuity_checks": continuity_checked,
                "continuity_failures": continuity_failed,
                "known_amount_fraction": (sum(t.amount is not None for t in transactions) / len(transactions)) if transactions else 0.0,
            },
        )
