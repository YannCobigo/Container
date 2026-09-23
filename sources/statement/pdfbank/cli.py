from __future__ import annotations

import json
from pathlib import Path

import typer

from .extractor import StatementExtractor
from .logging_utils import configure_logging
from .models import StatementResult
from .review import serve_review
from .validation import FinancialValidator
from .versions import version_report

app = typer.Typer(no_args_is_help=True, help="Offline, auditable bank-statement PDF extraction")


def _emit(obj, as_json: bool):
    if as_json:
        if hasattr(obj, "model_dump_json"):
            typer.echo(obj.model_dump_json(indent=2))
        else:
            typer.echo(json.dumps(obj, indent=2, default=str))


@app.command()
def inspect(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON"),
):
    extractor = StatementExtractor(ocr=False, tables=False, validation=False)
    result = extractor.inspector.inspect(pdf)
    if json_output:
        _emit(result, True)
    else:
        typer.echo(f"File: {result.source_filename}")
        typer.echo(f"Pages: {result.page_count}; kind: {result.document_kind}; encrypted: {result.encrypted}")
        typer.echo(f"Financial-statement likelihood: {result.financial_statement_likelihood:.2f}")
        for p in result.pages:
            typer.echo(f"  p{p.page_number}: {p.kind}; text={p.native_char_count} chars; image={p.image_area_ratio:.2f}; OCR-needed={p.needs_ocr}")


@app.command()
def extract(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    no_ocr: bool = typer.Option(False, "--no-ocr"),
    no_tables: bool = typer.Option(False, "--no-tables"),
    no_validation: bool = typer.Option(False, "--no-validation"),
    json_summary: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    layout_backend: str = typer.Option("heuristic", "--layout-backend", help="heuristic or onnx"),
    layout_model: Path | None = typer.Option(None, "--layout-model", help="Local ONNX model path"),
    layout_config: Path | None = typer.Option(None, "--layout-config", help="Local ONNX adapter JSON"),
):
    configure_logging(verbose=verbose)
    extractor = StatementExtractor(
        ocr=not no_ocr, tables=not no_tables, validation=not no_validation,
        layout_backend=layout_backend, layout_model=layout_model, layout_config=layout_config,
    )
    result = extractor.process(pdf)
    extractor.write_outputs(result, pdf, output)
    summary = {
        "source": pdf.name,
        "output": str(output),
        "transactions": len(result.transactions),
        "document_kind": result.inspection.document_kind,
        "validation_status": result.validation.status if result.validation else "not_run",
        "ocr_pages": result.debug.get("ocr_pages"),
    }
    if json_summary:
        _emit(summary, True)
    else:
        typer.echo(f"Extracted {len(result.transactions)} transactions -> {output}")
        typer.echo(f"Validation: {summary['validation_status']}; OCR pages: {summary['ocr_pages']}")


@app.command()
def batch(
    input_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Path = typer.Option(..., "--output", "-o"),
    json_summary: bool = typer.Option(False, "--json"),
    layout_backend: str = typer.Option("heuristic", "--layout-backend", help="heuristic or onnx"),
    layout_model: Path | None = typer.Option(None, "--layout-model", help="Local ONNX model path"),
    layout_config: Path | None = typer.Option(None, "--layout-config", help="Local ONNX adapter JSON"),
):
    extractor = StatementExtractor(layout_backend=layout_backend, layout_model=layout_model, layout_config=layout_config)
    results = extractor.process_directory(input_dir, output)
    summary = [{"source": r.document.source_filename, "transactions": len(r.transactions), "validation": r.validation.status if r.validation else None} for r in results]
    if json_summary:
        _emit(summary, True)
    else:
        typer.echo(f"Processed {len(results)} PDFs into {output}")


@app.command()
def ocr(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    output: Path | None = typer.Option(None, "--output", "-o", help="OCR JSON output"),
    searchable_pdf: Path | None = typer.Option(None, "--searchable-pdf", help="Optional OCRmyPDF derivative"),
    page: int | None = typer.Option(None, "--page", min=1),
):
    extractor = StatementExtractor(ocr=True, tables=False, validation=False)
    inspection = extractor.inspector.inspect(pdf)
    pages = [page] if page else [p.page_number for p in inspection.pages if p.needs_ocr]
    if not pages and page is None:
        typer.echo("No pages require OCR according to inspection.")
    payload = [extractor.ocr_service.ocr(pdf, p).model_dump() for p in pages]
    text = json.dumps(payload, indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)
    if searchable_pdf:
        extractor.create_searchable_copy(pdf, searchable_pdf)


@app.command()
def tables(
    pdf: Path = typer.Argument(..., exists=True, readable=True),
    json_output: bool = typer.Option(False, "--json"),
):
    extractor = StatementExtractor(ocr=True, tables=True, validation=False)
    result = extractor.process(pdf)
    if json_output:
        typer.echo(json.dumps([t.model_dump() for t in result.tables], indent=2, default=str))
    else:
        typer.echo(f"Detected {len(result.tables)} tables")
        for i, table in enumerate(result.tables, 1):
            typer.echo(f"  {i}. page {table.page_number}: {table.extraction_method}, rows={len(table.rows)}, confidence={table.confidence:.2f}")


@app.command()
def validate(
    statement_json: Path = typer.Argument(..., exists=True, readable=True),
    tolerance: float = typer.Option(0.01, "--tolerance"),
    json_output: bool = typer.Option(False, "--json"),
):
    result = StatementResult.model_validate_json(statement_json.read_text(encoding="utf-8"))
    validation = FinancialValidator(tolerance).validate(result.balances, result.transactions)
    if json_output:
        _emit(validation, True)
    else:
        typer.echo(f"Status: {validation.status}; reconciliation difference: {validation.reconciliation_difference}")
        for issue in validation.issues:
            typer.echo(f"  {issue.severity}: {issue.code}: {issue.message}")


@app.command(name="versions")
def versions_cmd(json_output: bool = typer.Option(False, "--json")):
    report = version_report()
    if json_output:
        _emit(report, True)
    else:
        typer.echo(f"Python: {report['python']}")
        typer.echo(f"OS: {report['os_release'].get('PRETTY_NAME', report['platform'])}")
        for name, version in report["python_packages"].items():
            typer.echo(f"{name}: {version or 'not installed'}")
        for name, version in report["commands"].items():
            typer.echo(f"{name}: {version or 'not available'}")
        typer.echo(f"models: {json.dumps(report['models'], sort_keys=True)}")


@app.command()
def review(
    result_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    bind: str = typer.Option("127.0.0.1", "--bind"),
    port: int = typer.Option(8765, "--port", min=1024, max=65535),
):
    """Serve the self-contained report locally and persist corrections as annotations.jsonl."""
    serve_review(result_dir, bind=bind, port=port)


if __name__ == "__main__":
    app()
