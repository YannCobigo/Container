from __future__ import annotations

from .models import StatementResult


def apply_validation_confidence(result: StatementResult) -> None:
    """Lower confidence for validation failures; never overwrite extracted values."""
    if result.validation is None:
        return
    status = result.validation.status
    for txn in result.transactions:
        factor = 1.0
        if txn.validation_status in {"reconciliation_failed", "statement_reconciliation_failed"}:
            factor = 0.75
        elif txn.validation_status == "warning":
            factor = 0.88
        txn.confidence = max(0.0, min(1.0, txn.confidence * factor))
        for prov in txn.field_provenance.values():
            prov.confidence = max(0.0, min(1.0, prov.confidence * factor))
            prov.validation_status = txn.validation_status

    # Propagate validation state to statement-level balance provenance.
    for prov in result.provenance:
        if prov.field.startswith("balances."):
            if status == "validated":
                prov.validation_status = "passed"
            elif status == "failed":
                prov.validation_status = "reconciliation_failed"
                prov.confidence *= 0.80
