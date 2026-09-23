from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

from pdfbank import StatementExtractor
from pdfbank.utils import similarity
from pdfbank.versions import version_report


def txn_match(expected, actual):
    if expected.get("date") != actual.date:
        return False
    if expected.get("amount") is None or actual.amount is None or abs(expected["amount"] - actual.amount) > 0.01:
        return False
    return similarity(expected.get("description"), actual.description) >= 0.55


def evaluate_one(pdf: Path, truth_path: Path, output_dir: Path | None = None):
    truth = json.loads(truth_path.read_text())
    extractor = StatementExtractor()
    started = time.perf_counter(); result = extractor.process(pdf); elapsed = time.perf_counter() - started
    if output_dir:
        extractor.write_outputs(result, pdf, output_dir / pdf.stem)

    expected_tx = truth.get("transactions", [])
    matched_expected = set(); matched_actual = set()
    amount_correct = date_correct = 0
    for ei, exp in enumerate(expected_tx):
        for ai, act in enumerate(result.transactions):
            if ai in matched_actual:
                continue
            if txn_match(exp, act):
                matched_expected.add(ei); matched_actual.add(ai)
                amount_correct += int(abs(exp["amount"] - act.amount) <= 0.01)
                date_correct += int(exp["date"] == act.date)
                break
    precision = len(matched_actual) / len(result.transactions) if result.transactions else 0.0
    recall = len(matched_expected) / len(expected_tx) if expected_tx else 0.0

    expected_fields = {}
    actual_fields = {}
    for section in ["document", "account", "balances"]:
        for key, value in truth.get(section, {}).items():
            if value is not None:
                expected_fields[f"{section}.{key}"] = value
                actual_fields[f"{section}.{key}"] = getattr(getattr(result, section), key)
    tp = sum(actual_fields[k] == v or (isinstance(v, (int,float)) and actual_fields[k] is not None and abs(float(actual_fields[k]) - float(v)) <= 0.01) for k, v in expected_fields.items())
    predicted_nonnull = sum(v is not None for v in actual_fields.values())
    field_precision = tp / predicted_nonnull if predicted_nonnull else 0.0
    field_recall = tp / len(expected_fields) if expected_fields else 0.0
    ocr_confs = [p.ocr.mean_confidence for p in result.pages if p.ocr]
    return {
        "file": pdf.name,
        "document_kind": result.inspection.document_kind,
        "field_precision": round(field_precision, 4),
        "field_recall": round(field_recall, 4),
        "transaction_precision": round(precision, 4),
        "transaction_recall": round(recall, 4),
        "amount_accuracy": round(amount_correct / len(expected_tx), 4) if expected_tx else 0.0,
        "date_accuracy": round(date_correct / len(expected_tx), 4) if expected_tx else 0.0,
        "balance_reconciliation": result.validation.status if result.validation else None,
        "ocr_confidence": round(statistics.mean(ocr_confs), 4) if ocr_confs else None,
        "processing_seconds": round(elapsed, 4),
        "expected_transactions": len(expected_tx),
        "actual_transactions": len(result.transactions),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", type=Path, default=Path(__file__).resolve().parent / "fixtures")
    ap.add_argument("--truth", type=Path, default=Path(__file__).resolve().parent / "ground_truth")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--results", type=Path)
    args = ap.parse_args()
    rows = []
    for truth_path in sorted(args.truth.glob("*.json")):
        pdf = args.fixtures / f"{truth_path.stem}.pdf"
        if pdf.exists():
            rows.append(evaluate_one(pdf, truth_path, args.output))
    summary = {
        "runtime_environment": version_report(),
        "cases": rows,
        "macro": {
            metric: round(statistics.mean(r[metric] for r in rows), 4) if rows else 0.0
            for metric in ["field_precision", "field_recall", "transaction_precision", "transaction_recall", "amount_accuracy", "date_accuracy"]
        },
        "note": "Synthetic deterministic fixtures only; these results are not a claim about real-world bank-statement accuracy.",
    }
    text = json.dumps(summary, indent=2)
    if args.results:
        args.results.parent.mkdir(parents=True, exist_ok=True); args.results.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
