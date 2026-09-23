# pdfbank Apptainer / Singularity image

## Purpose

This project builds an immutable Apptainer/Singularity image for offline, auditable extraction of
structured financial data from bank-statement PDFs. The core path is deterministic and does not
call cloud OCR, cloud LLMs, proprietary APIs, telemetry services, or external web services at
runtime. Native PDF text is preferred whenever it is sufficiently reliable. OCR is selected on a
page-by-page basis only when inspection indicates that it is required.

The image also contains common forensic and operator tools: Poppler (`pdfinfo`, `pdftotext`,
`pdftoppm`, `pdfimages`), qpdf, MuPDF `mutool`, Tesseract, OCRmyPDF, Ghostscript, LibreOffice,
Emacs, Firefox ESR, curl, wget, rsync, jq, and standard shell utilities. GUI applications require
the host's X11/Wayland environment to be bound in the usual Apptainer manner; they are not used by
the extraction pipeline.

## Why Debian 13

The image is based on Debian 13 (trixie), with Python 3.13. The OCI base is pinned by digest and the
APT sources are pinned to the Debian snapshot timestamp `20260901T000000Z`. This gives current PDF
and OCR system packages while retaining a stable, reproducible OS baseline. Python top-level
runtime packages are exact-pinned in `sources/statement/requirements.txt` and the built image stores
both `pip freeze --all` and a complete `dpkg-query` manifest in `/opt/pdfbank/build-info`.

A SIF build cannot be guaranteed bit-identical across different Apptainer versions, compressor
implementations, architectures, or upstream registry behavior. For high-assurance archival builds,
retain the SIF itself, its SHA-256 digest, the definition file, the wheelhouse used at build time,
and `/opt/pdfbank/build-info/*`. The supplied `build.sh` uses Apptainer's `--reproducible` option.

## Directory layout

```text
/serv/Container/
├── definitions/statement/
│   ├── statement.def
│   ├── build.sh
│   ├── environment.yml
│   └── README.md
├── sources/statement/
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── requirements-test.txt
│   └── pdfbank/
├── tests/statement/
│   ├── generate_fixtures.py
│   ├── evaluate.py
│   ├── annotation_schema.json
│   ├── fixtures/
│   ├── ground_truth/
│   └── test_pdfbank.py
├── examples/statement/
│   ├── input/
│   └── output/
└── models/statement/
    ├── manifest.json
    └── install_model.sh
```

## Build

Run the build from `/serv/Container`, because the `%files` paths in the definition are deliberately
relative to that build context:

```bash
cd /serv/Container
./definitions/statement/build.sh definitions/statement/pdfbank.sif
sha256sum definitions/statement/pdfbank.sif > definitions/statement/pdfbank.sif.sha256
```

On installations where privileged builds are unavailable, use the site's supported fakeroot or
remote-build configuration. The definition itself does not require a writable runtime filesystem.

The `%test` section performs an in-image smoke test against the deterministic native statement
fixture. For a development build that omits `%test`, Apptainer supports `--notest`, but production
release images should run it.

## Offline execution

The code performs no network calls in the core pipeline. Apptainer normally shares the host network,
so for process-level network isolation use Apptainer's `none` CNI network:

```bash
apptainer exec --cleanenv --net --network none \
  --bind "$PWD/statements:/input:ro" \
  --bind "$PWD/results:/output" \
  definitions/statement/pdfbank.sif \
  pdfbank batch /input --output /output
```

`--network none` leaves only loopback networking. If a site policy disables network namespaces, the
pipeline remains offline-by-design, but operating-system enforcement must be supplied by the host.

## Single-document use

```bash
apptainer exec pdfbank.sif pdfbank inspect statement.pdf
apptainer exec pdfbank.sif pdfbank inspect statement.pdf --json

apptainer exec pdfbank.sif \
  pdfbank extract statement.pdf --output results/
```

The extraction directory contains at least:

```text
transactions.csv
statement.json
validation.json
inspection.json
extraction_report.html
```

`statement.json` retains normalized pages, OCR words, tables, transactions, validation data, and
provenance. `transactions.csv` has one transaction per row and includes source file, page, source
text, confidence, method, validation status, and warnings.

## Directory processing

```bash
apptainer exec pdfbank.sif \
  pdfbank batch /data/statements --output /data/results
```

Each input PDF receives a subdirectory named after the PDF stem. Inputs are opened read-only by the
pipeline. The original is never rewritten.

## CLI

```text
pdfbank inspect PDF [--json]
pdfbank extract PDF --output DIR [--no-ocr] [--no-tables] [--no-validation] [--layout-backend heuristic|onnx] [--json]
pdfbank batch INPUT_DIR --output DIR [--layout-backend heuristic|onnx] [--json]
pdfbank ocr PDF [--page N] [--output OCR.json] [--searchable-pdf derivative.pdf]
pdfbank tables PDF [--json]
pdfbank validate statement.json [--tolerance 0.01] [--json]
pdfbank versions [--json]
pdfbank review RESULT_DIR [--bind 127.0.0.1] [--port 8765]
```

The `--searchable-pdf` option invokes OCRmyPDF to make a new derivative. It never replaces the
source PDF. The main extraction path uses Tesseract directly because word-level coordinates and
confidence need to remain available in the normalized representation.

## Python API

```python
from pdfbank import StatementExtractor

extractor = StatementExtractor(ocr=True, tables=True, validation=True)
result = extractor.process("statement.pdf")

result.to_json("statement.json")
result.to_csv("transactions.csv")

print(result.pages)
print(result.text)
print(result.ocr)
print(result.tables)
print(result.transactions)
print(result.validation)
print(result.provenance)
```

Directory mode:

```python
extractor.process_directory(
    input_dir="statements/",
    output_dir="results/",
)
```

## Processing architecture

The implementation is intentionally staged:

```text
PDF
 |
 v
Document Inspector                         PASS 1
 |-- native text / geometry                PASS 2
 |-- OCR only where required               PASS 3
 v
Normalized Document Representation
 |
 +-- deterministic layout analysis         PASS 4
 +-- table detection                       PASS 5
 +-- transaction candidate detection       PASS 6
 +-- semantic statement extraction         PASS 7
 +-- accounting validation/reconciliation  PASS 8
 +-- confidence adjustment                 PASS 9
 +-- CSV / JSON / HTML export              PASS 10
```

Downstream code consumes `NormalizedPage.words` and `NormalizedPage.lines`; it does not need to know
whether the selected source was native PDF text or OCR. Both representations remain available for
debugging.

## PDF inspection decisions

Each page records dimensions, PDF rotation, native character count, text density, number and area of
embedded images, an estimated embedded-image DPI where calculable, scan likelihood, OCR-present
likelihood, table likelihood, and whether native text is considered reliable. The document records
encryption status, metadata, SHA-256, page count, document kind (`digital`, `scanned`, `mixed`, or
`unknown`), and a conservative financial-statement likelihood.

These are heuristics, not claims about authoring history. A PDF may contain unusual invisible text,
vectorized glyphs, malformed font maps, or multiple image layers. The values are evidence used to
choose a processing path and remain visible in `inspection.json`.

## Image preprocessing and OCR

The Tesseract backend renders scan-like pages at 300 DPI through MuPDF, then evaluates multiple
conservative candidates: grayscale/deskew, contrast enhancement, adaptive thresholding, and
denoise/sharpen. Candidate selection combines mean OCR confidence, token count, alphanumeric
plausibility, and financial-token density. Spatial coordinates are transformed back into PDF point
coordinates and retained for every OCR word.

Available preprocessing primitives also include border removal, perspective correction, resolution
normalization, CLAHE contrast enhancement, adaptive thresholding, denoising, deskewing and
sharpening. Perspective correction is deliberately conservative and is not applied automatically to
every page.

Tesseract is implemented behind an `OCRBackend` abstraction. A second engine can be added without
changing the normalized page model or transaction parser.

## Table extraction

The table layer uses complementary methods:

* pdfplumber line-based and text-based extraction for native PDFs;
* Camelot lattice/stream extraction as a second native-PDF strategy;
* OpenCV morphology for ruled grids in image-derived pages;
* word-coordinate row reconstruction for borderless/OCR tables.

Every `ExtractedTable` records rows, optional cells and cell bounding boxes, page, source text,
extraction method, and confidence. Table extraction is evidence for downstream parsing; the
transaction parser does not assume every statement has a visible table border.

Camelot 2 also offers optional ML/OCR table backends. They are intentionally not enabled by default
because the core design prioritizes deterministic methods and a smaller auditable dependency set.
They can be added to a controlled model-enabled variant after evaluation on the target banks.

## Layout models

The default layout backend is deterministic and labels page/header/footer/account/summary/table-header
regions from coordinates and lexical evidence. No model download occurs at runtime.

`/models/pdfbank` is the model mount point. `models/statement/install_model.sh` is a build-time helper
that downloads a model only when explicitly invoked and requires an expected SHA-256 before the file
is accepted. For regulated use, pre-stage model weights outside the image, verify their license and
hash, and bind them read-only:

```bash
apptainer exec --bind /secure/models/pdfbank:/models/pdfbank:ro pdfbank.sif ...
```

The Python package includes an executable optional ONNX layout backend. The model is accompanied by
a JSON adapter contract declaring input dimensions, labels, confidence/NMS thresholds, coordinate
mode, and either an `xyxy_score_class` or YOLOv8-style output tensor. An example is provided at
`models/statement/layout_model.example.json`. No tensor convention is guessed. Invoke it with
`--layout-backend onnx --layout-model MODEL.onnx --layout-config CONFIG.json`, or place the files at
`$PDFBANK_MODEL_DIR/layout/model.onnx` and `config.json`. The deterministic backend remains the
production default until the selected learned model is separately validated on the target banks.

## Canonical information model and provenance

Canonical values are stored under `document`, `account`, `balances`, and `transactions`. Missing
values remain JSON `null`. Extractors do not synthesize absent account fields or financial values.

Every important extracted field is also represented by a provenance record containing:

```json
{
  "field": "balances.closing_balance",
  "value": 3216.94,
  "confidence": 0.92,
  "page": 1,
  "bbox": [54.0, 355.0, 170.0, 367.0],
  "source_text": "Closing balance $3,216.94",
  "method": "labeled_amount_regex",
  "validation_status": "passed",
  "warnings": []
}
```

Transaction fields also carry their own `field_provenance` dictionary. The transaction row retains
its page, bounding box, source text, extraction method, confidence, validation status, and warnings.

## Transaction parsing and uncertainty

The generic parser handles date/description/amount/balance layouts, debit/credit columns, negative
values, parentheses, CR/DR notation, currency symbols, thousands separators, decimal commas,
multiline descriptions, repeated page headers, and continuation text across page boundaries.
Column positions are inferred from transaction-table headers when available. A positive number is
not automatically treated as a credit; lexical or column evidence is required, otherwise the
transaction is marked `unknown` and confidence is lowered.

This generic parser is meant to be extended with bank-specific profiles for recurring institutions.
Production use against a new bank should add fixtures and a profile only after representative
statements are annotated.

## Validation

The validator checks running balances where available, whole-statement reconciliation, duplicate
semantic transaction tuples, missing/malformed dates and amounts, summary deposit/withdrawal totals,
and date ordering. The configured default tolerance is one cent.

Validation never rewrites a value to force reconciliation. A mismatch lowers confidence and records
warnings or errors. A closing balance that disagrees with transaction arithmetic remains the
extracted closing balance and is returned with `reconciliation_failed` evidence.

## Human review

`extraction_report.html` is self-contained and loads no external assets. Low-confidence or warned
transactions show an image crop of the original PDF region beside editable fields.

To persist review annotations locally:

```bash
pdfbank review results/ --bind 127.0.0.1 --port 8765
```

The report posts corrections to the loopback-only server, which appends `annotations.jsonl` in the
result directory. When the HTML file is opened directly rather than through the local server, a
correction is downloaded as a small JSON annotation file instead. Annotations do not silently
replace extraction output; they are separate evidence suitable for regression fixtures or later
training.

## Privacy and logging

The pipeline logs page numbers, processing methods, counts and exception classes. It does not log
full transaction descriptions or PII in routine messages. A redacting formatter masks long digit
sequences if an exception message happens to contain an account-like value.

JSON/CSV outputs necessarily contain extracted financial data because that is the requested product.
Protect the output directory with the same controls as the source statements. Do not place real
statements, outputs, annotations, or model-training corpora in public source control.

## Test suite

From the source tree outside the image:

```bash
cd /serv/Container/sources/statement
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
pip install --no-deps -e .
python ../../tests/statement/generate_fixtures.py
pytest -q ../../tests/statement
python ../../tests/statement/evaluate.py \
  --results ../../examples/statement/output/evaluation.json
```

The fixtures cover native PDF text, an image-only scan, mixed native/scanned pages, page rotation,
severe low-resolution degradation, debit/credit columns, parentheses, CR/DR, multiline descriptions,
multi-page continuation, missing/uncertain extraction behavior, duplicate detection, and deliberate
reconciliation failure.

For real-world evaluation, keep source PDFs and annotations in a private directory and point
`evaluate.py --fixtures ... --truth ...` at it. `annotation_schema.json` is the ground-truth format.
Metrics include field precision/recall, transaction precision/recall, amount accuracy, date accuracy,
reconciliation status, OCR confidence, and processing time.

The included executed evaluation has eight synthetic cases. Seven cases score 1.0 for annotated
field and transaction precision/recall, including native, scanned, rotated, mixed, multiline/
multipage, debit/credit, and deliberate reconciliation-failure documents. The deliberately severe
low-resolution scan scores 0.0 and returns no invented transactions; its OCR confidence is about
0.44 and validation reports `insufficient_data`. Counting that designed failure as zero gives macro
field precision/recall, transaction precision/recall, amount accuracy, and date accuracy of 0.875.
See `examples/statement/output/evaluation.json` for exact timings and the runtime dependency report.

These synthetic results are only regression measurements. They are not a claim about unseen or
real-world bank statements. Real-world accuracy must be measured separately by institution, layout,
statement vintage, locale, and scanner condition. The executed evaluation environment also differs
from the exact target pins in `requirements.txt`; the target SIF must rerun `%test` and evaluation
after it is built.

## Version reporting

```bash
pdfbank versions
pdfbank versions --json
```

The command reports Python and OS information; PyMuPDF, pypdf, pdfplumber, Camelot, OpenCV,
Tesseract wrapper, OCRmyPDF, Pillow, NumPy, pandas, Pydantic and Typer versions; Poppler,
Tesseract, qpdf, MuPDF, Ghostscript, OCRmyPDF CLI, LibreOffice, Emacs and Firefox versions; plus the
model manifest. The same report is stored at build time in `/opt/pdfbank/build-info/versions.json`.

## Known failure modes

The system intentionally favors explicit failure over plausible-looking fabrication. Important
remaining limitations include very low-resolution raster text (the included severe degradation
fixture is designed to fail), password-locked PDFs unless the operator supplies a separately
decrypted working copy, handwriting, non-Latin languages unless their Tesseract packs are
installed, unusual locale/date conventions not represented by a bank profile, vector-outline text
with no extractable characters, complex perspective photographs, statements whose transaction sign
can only be inferred from proprietary bank semantics, and table layouts that cannot be segmented by
native geometry or OCR coordinates.

A high OCR confidence score is not equivalent to financial correctness. Final acceptance should be
based on accounting reconciliation, field provenance, bank-specific regression tests, and review of
unresolved warnings.
