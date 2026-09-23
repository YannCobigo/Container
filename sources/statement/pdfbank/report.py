from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path

import fitz
from PIL import Image

from .models import StatementResult


def _crop_data_uri(pdf_path: str | Path, page_number: int, bbox, padding: float = 12, dpi: int = 150) -> str | None:
    if not bbox:
        return None
    try:
        doc = fitz.open(str(pdf_path))
        page = doc[page_number - 1]
        rect = fitz.Rect(*bbox)
        rect.x0 = max(page.rect.x0, rect.x0 - padding)
        rect.y0 = max(page.rect.y0, rect.y0 - padding)
        rect.x1 = min(page.rect.x1, rect.x1 + padding)
        rect.y1 = min(page.rect.y1, rect.y1 + padding)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), clip=rect, alpha=False)
        data = pix.tobytes("png")
        doc.close()
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def build_html_report(result: StatementResult, source_pdf: str | Path, review_threshold: float = 0.85) -> str:
    doc = result.document
    val = result.validation
    fields = [
        ("Institution", doc.institution, "document.institution"),
        ("Statement period", f"{doc.statement_start_date or '?'} – {doc.statement_end_date or '?'}", "document.statement_start_date"),
        ("Account holder", result.account.account_holder, "account.account_holder"),
        ("Account", result.account.masked_account_number, "account.masked_account_number"),
        ("Opening balance", result.balances.opening_balance, "balances.opening_balance"),
        ("Closing balance", result.balances.closing_balance, "balances.closing_balance"),
    ]
    pmap = {p.field: p for p in result.provenance}

    field_rows = []
    for label, value, key in fields:
        prov = pmap.get(key)
        conf = prov.confidence if prov else 0.0
        status = "✓" if conf >= review_threshold else "⚠"
        field_rows.append(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value) if value is not None else 'unknown')}</td>"
            f"<td>{conf:.2f}</td><td>{status}</td></tr>"
        )

    txn_rows = []
    for t in result.transactions:
        needs_review = t.confidence < review_threshold or t.validation_status not in {"validated", "unvalidated"} or bool(t.warnings)
        crop = _crop_data_uri(source_pdf, t.page_number, t.bounding_box) if needs_review else None
        detail = ""
        if needs_review:
            crop_html = f'<img class="crop" src="{crop}" alt="source region">' if crop else "<em>Crop unavailable</em>"
            original = html.escape(t.source_text)
            detail = f"""
            <details><summary>Review source / annotate</summary>
              <div class="review-grid"><div>{crop_html}</div><div>
                <div><b>Source:</b> <code>{original}</code></div>
                <label>Corrected date <input data-field="date" value="{html.escape(t.date or '')}"></label>
                <label>Corrected description <input data-field="description" value="{html.escape(t.description or '')}"></label>
                <label>Corrected amount <input data-field="amount" value="{'' if t.amount is None else t.amount}"></label>
                <label>Corrected balance <input data-field="balance_after_transaction" value="{'' if t.balance_after_transaction is None else t.balance_after_transaction}"></label>
                <button onclick="saveAnnotation(this, '{t.transaction_id}')">Save annotation</button>
              </div></div>
            </details>"""
        txn_rows.append(
            f"<tr data-txid='{t.transaction_id}'><td>{html.escape(t.date or '?')}</td>"
            f"<td>{html.escape(t.description or '')}</td><td>{'' if t.amount is None else f'{t.amount:.2f}'}</td>"
            f"<td>{'' if t.balance_after_transaction is None else f'{t.balance_after_transaction:.2f}'}</td>"
            f"<td>{t.page_number}</td><td>{t.confidence:.2f}</td><td>{html.escape(t.validation_status)}</td></tr>"
            f"<tr class='detail'><td colspan='7'>{detail}</td></tr>"
        )

    validation_text = html.escape(val.model_dump_json(indent=2) if val else "not run")
    embedded_meta = json.dumps({"document_id": doc.document_id, "source_filename": doc.source_filename})
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>pdfbank extraction report</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem;line-height:1.35;color:#202124}} table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border-bottom:1px solid #ddd;text-align:left;padding:.45rem;vertical-align:top}} th{{background:#f6f6f6}} .crop{{max-width:100%;border:1px solid #aaa}} .review-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}} label{{display:block;margin:.35rem 0}} input{{width:95%}} code{{white-space:pre-wrap}} pre{{background:#f6f6f6;padding:1rem;overflow:auto}} .detail td{{border-bottom:0;padding-top:0}} .muted{{color:#666}}
</style></head><body>
<h1>Extraction report</h1>
<p><b>Statement:</b> {html.escape(doc.source_filename)} &nbsp; <span class="muted">Document ID: {doc.document_id}</span></p>
<h2>Key fields</h2><table><tbody>{''.join(field_rows)}</tbody></table>
<h2>Transactions</h2>
<table><thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Balance</th><th>Page</th><th>Confidence</th><th>Validation</th></tr></thead><tbody>{''.join(txn_rows)}</tbody></table>
<h2>Validation</h2><pre>{validation_text}</pre>
<p class="muted">No external resources are loaded. Corrections are posted to the local <code>pdfbank review</code> server when served through it; otherwise they are downloaded as a JSON annotation file.</p>
<script>
const reportMeta={embedded_meta};
async function saveAnnotation(button, txid){{
  const row=button.closest('tr').previousElementSibling;
  const panel=button.parentElement;
  const fields={{}};
  panel.querySelectorAll('input[data-field]').forEach(i=>fields[i.dataset.field]=i.value);
  const annotation={{...reportMeta, transaction_id:txid, corrected_fields:fields, created_at:new Date().toISOString()}};
  try {{
    const response=await fetch('/annotations',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(annotation)}});
    if(!response.ok) throw new Error('not served by review endpoint');
    button.textContent='Saved';
  }} catch(e) {{
    const blob=new Blob([JSON.stringify(annotation,null,2)],{{type:'application/json'}});
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=`annotation_${{txid}}.json`; a.click();
  }}
}}
</script></body></html>"""
