# Test and evaluation strategy

`generate_fixtures.py` creates deterministic synthetic PDFs covering native text, image-only scans,
mixed pages, rotation metadata, severe low-resolution degradation, debit/credit columns,
multiline descriptions, cross-page continuation, and a deliberately inconsistent closing balance.

`test_pdfbank.py` is the regression suite. `evaluate.py` compares output with JSON ground truth and
reports field precision/recall, transaction precision/recall, amount/date accuracy, reconciliation
status, OCR confidence, and wall-clock processing time.

For real statements, copy an input PDF into a private evaluation directory and create a ground-truth
JSON document matching `annotation_schema.json`. Do not commit real financial statements to source
control. Run the same evaluator by pointing `--fixtures` and `--truth` at those private directories.
The evaluator never transmits data.
