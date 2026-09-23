from __future__ import annotations

import json
from pathlib import Path

import fitz
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageFilter
import io

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "fixtures"
GT = ROOT / "ground_truth"


def c_new(path: Path):
    return canvas.Canvas(str(path), pagesize=letter, invariant=1, pageCompression=1)


def header(c, period="06/01/2025 - 06/30/2025", opening="$1,000.00"):
    c.setFont("Helvetica-Bold", 16)
    c.drawString(54, 744, "ACME COMMUNITY BANK")
    c.setFont("Helvetica", 11)
    c.drawString(54, 721, "Monthly Checking Statement")
    c.drawString(54, 700, "Statement for: John Smith")
    c.drawString(54, 681, "Account number: ****1234")
    c.drawString(54, 662, f"Statement period: {period}")
    c.drawString(54, 640, f"Opening balance {opening}")


def table_header(c, debit_credit=False, y=610):
    c.setFont("Helvetica-Bold", 9)
    c.drawString(54, y, "Date")
    c.drawString(128, y, "Description")
    if debit_credit:
        c.drawRightString(400, y, "Debit")
        c.drawRightString(475, y, "Credit")
        c.drawRightString(555, y, "Balance")
    else:
        c.drawRightString(465, y, "Amount")
        c.drawRightString(555, y, "Balance")
    c.line(54, y - 4, 558, y - 4)


def draw_standard(path: Path, bad_closing=False):
    c = c_new(path)
    header(c)
    table_header(c)
    c.setFont("Helvetica", 9)
    rows = [
        ("06/03/2025", "WHOLE FOODS PURCHASE", "-$84.31", "$915.69"),
        ("06/04/2025", "PAYROLL DEPOSIT", "+$2,400.00", "$3,315.69"),
        ("06/07/2025", "ATM WITHDRAWAL", "($100.00)", "$3,215.69"),
        ("06/20/2025", "INTEREST CREDIT", "$1.25 CR", "$3,216.94"),
    ]
    y = 585
    for d, desc, amount, balance in rows:
        c.drawString(54, y, d); c.drawString(128, y, desc); c.drawRightString(465, y, amount); c.drawRightString(555, y, balance)
        y -= 24
    closing = "$3,999.99" if bad_closing else "$3,216.94"
    c.drawString(54, 460, "Total deposits $2,401.25")
    c.drawString(54, 442, "Total withdrawals $184.31")
    c.drawString(54, 424, f"Closing balance {closing}")
    c.showPage(); c.save()


def draw_debit_credit(path: Path):
    c = c_new(path)
    header(c, opening="$5,000.00")
    table_header(c, debit_credit=True)
    c.setFont("Helvetica", 9)
    rows = [
        ("06/01/2025", "RENT PAYMENT", "1,200.00", "", "3,800.00"),
        ("06/05/2025", "CLIENT PAYMENT", "", "2,500.00", "6,300.00"),
        ("06/10/2025", "MONTHLY FEE", "15.00", "", "6,285.00"),
    ]
    y = 585
    for d, desc, debit, credit, bal in rows:
        c.drawString(54, y, d); c.drawString(128, y, desc)
        if debit: c.drawRightString(400, y, debit)
        if credit: c.drawRightString(475, y, credit)
        c.drawRightString(555, y, bal)
        y -= 24
    c.drawString(54, 490, "Closing balance $6,285.00")
    c.showPage(); c.save()


def draw_multiline(path: Path):
    c = c_new(path)
    header(c, opening="$2,000.00")
    table_header(c)
    c.setFont("Helvetica", 9)
    c.drawString(54, 585, "06/02/2025"); c.drawString(128, 585, "ONLINE PURCHASE MERCHANT"); c.drawRightString(465, 585, "-$50.00"); c.drawRightString(555, 585, "$1,950.00")
    c.drawString(128, 568, "REFERENCE 998877 SECOND LINE")
    c.drawString(54, 540, "06/15/2025"); c.drawString(128, 540, "TRANSFER CREDIT"); c.drawRightString(465, 540, "+$300.00"); c.drawRightString(555, 540, "$2,250.00")
    c.showPage()
    c.setFont("Helvetica-Bold", 9); c.drawString(54, 740, "Transactions continued")
    table_header(c, y=715)
    c.setFont("Helvetica", 9)
    c.drawString(128, 691, "CONTINUED TRANSFER MEMO")
    c.drawString(54, 662, "06/28/2025"); c.drawString(128, 662, "CHECK 1042"); c.drawRightString(465, 662, "-$125.00"); c.drawRightString(555, 662, "$2,125.00")
    c.drawString(54, 620, "Closing balance $2,125.00")
    c.showPage(); c.save()


def rasterize_pdf(source: Path, out: Path, dpi=180, rotate=0, blur=False):
    src = fitz.open(str(source))
    outdoc = fitz.open()
    for page in src:
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        if blur:
            image = image.resize((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.BILINEAR)
            image = image.filter(ImageFilter.GaussianBlur(0.45))
        if rotate:
            image = image.rotate(rotate, expand=True, fillcolor="white")
        buf = io.BytesIO(); image.save(buf, format="PNG")
        p = outdoc.new_page(width=612, height=792)
        p.insert_image(p.rect, stream=buf.getvalue())
        if rotate == 90:
            # Metadata rotation test while retaining an image-only page.
            p.set_rotation(90)
    outdoc.save(str(out), garbage=4, deflate=True)
    outdoc.close(); src.close()



def draw_mixed_source(path: Path):
    c = c_new(path)
    header(c)
    table_header(c)
    c.setFont("Helvetica", 9)
    rows1 = [
        ("06/03/2025", "WHOLE FOODS PURCHASE", "-$84.31", "$915.69"),
        ("06/04/2025", "PAYROLL DEPOSIT", "+$2,400.00", "$3,315.69"),
    ]
    y = 585
    for d, desc, amount, balance in rows1:
        c.drawString(54, y, d); c.drawString(128, y, desc); c.drawRightString(465, y, amount); c.drawRightString(555, y, balance); y -= 24
    c.showPage()
    c.setFont("Helvetica-Bold", 12); c.drawString(54, 744, "ACME COMMUNITY BANK - Transactions continued")
    table_header(c, y=715)
    c.setFont("Helvetica", 9)
    rows2 = [
        ("06/07/2025", "ATM WITHDRAWAL", "($100.00)", "$3,215.69"),
        ("06/20/2025", "INTEREST CREDIT", "$1.25 CR", "$3,216.94"),
    ]
    y = 690
    for d, desc, amount, balance in rows2:
        c.drawString(54, y, d); c.drawString(128, y, desc); c.drawRightString(465, y, amount); c.drawRightString(555, y, balance); y -= 24
    c.drawString(54, 620, "Total deposits $2,401.25")
    c.drawString(54, 602, "Total withdrawals $184.31")
    c.drawString(54, 584, "Closing balance $3,216.94")
    c.showPage(); c.save()


def make_mixed(source: Path, out: Path, dpi=180):
    src = fitz.open(str(source))
    outdoc = fitz.open()
    outdoc.insert_pdf(src, from_page=0, to_page=0)
    page = src[1]
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    p = outdoc.new_page(width=float(page.rect.width), height=float(page.rect.height))
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    outdoc.save(str(out), garbage=4, deflate=True)
    outdoc.close(); src.close()

def mixed_pdf(native: Path, scanned: Path, out: Path):
    a, b = fitz.open(str(native)), fitz.open(str(scanned))
    outdoc = fitz.open()
    outdoc.insert_pdf(a, from_page=0, to_page=0)
    outdoc.insert_pdf(b, from_page=0, to_page=0)
    outdoc.save(str(out), garbage=4, deflate=True)
    outdoc.close(); a.close(); b.close()


def gt(path: Path, opening, closing, txns):
    path.write_text(json.dumps({
        "document": {"statement_start_date": "2025-06-01", "statement_end_date": "2025-06-30"},
        "account": {"account_holder": "John Smith", "masked_account_number": "***1234"},
        "balances": {"opening_balance": opening, "closing_balance": closing},
        "transactions": txns,
    }, indent=2), encoding="utf-8")


def main():
    FIX.mkdir(parents=True, exist_ok=True); GT.mkdir(parents=True, exist_ok=True)
    native = FIX / "native_standard.pdf"
    draw_standard(native)
    draw_standard(FIX / "reconciliation_failure.pdf", bad_closing=True)
    debit = FIX / "debit_credit.pdf"; draw_debit_credit(debit)
    multi = FIX / "multiline_multipage.pdf"; draw_multiline(multi)
    rasterize_pdf(native, FIX / "scanned_statement.pdf", dpi=180)
    rasterize_pdf(native, FIX / "lowres_scan.pdf", dpi=96, blur=True)
    rasterize_pdf(native, FIX / "rotated_scan.pdf", dpi=180, rotate=90)
    mixed_source = FIX / "_mixed_source.pdf"; draw_mixed_source(mixed_source)
    make_mixed(mixed_source, FIX / "mixed_statement.pdf")
    mixed_source.unlink()

    std_tx = [
        {"date": "2025-06-03", "description": "WHOLE FOODS PURCHASE", "amount": -84.31, "balance_after_transaction": 915.69},
        {"date": "2025-06-04", "description": "PAYROLL DEPOSIT", "amount": 2400.00, "balance_after_transaction": 3315.69},
        {"date": "2025-06-07", "description": "ATM WITHDRAWAL", "amount": -100.00, "balance_after_transaction": 3215.69},
        {"date": "2025-06-20", "description": "INTEREST CREDIT", "amount": 1.25, "balance_after_transaction": 3216.94},
    ]
    for name in ["native_standard", "scanned_statement", "lowres_scan", "rotated_scan", "mixed_statement"]:
        gt(GT / f"{name}.json", 1000.0, 3216.94, std_tx)
    gt(GT / "debit_credit.json", 5000.0, 6285.0, [
        {"date": "2025-06-01", "description": "RENT PAYMENT", "amount": -1200.0, "balance_after_transaction": 3800.0},
        {"date": "2025-06-05", "description": "CLIENT PAYMENT", "amount": 2500.0, "balance_after_transaction": 6300.0},
        {"date": "2025-06-10", "description": "MONTHLY FEE", "amount": -15.0, "balance_after_transaction": 6285.0},
    ])
    gt(GT / "multiline_multipage.json", 2000.0, 2125.0, [
        {"date": "2025-06-02", "description": "ONLINE PURCHASE MERCHANT REFERENCE 998877 SECOND LINE", "amount": -50.0, "balance_after_transaction": 1950.0},
        {"date": "2025-06-15", "description": "TRANSFER CREDIT CONTINUED TRANSFER MEMO", "amount": 300.0, "balance_after_transaction": 2250.0},
        {"date": "2025-06-28", "description": "CHECK 1042", "amount": -125.0, "balance_after_transaction": 2125.0},
    ])
    gt(GT / "reconciliation_failure.json", 1000.0, 3999.99, std_tx)
    print(FIX)


if __name__ == "__main__":
    main()
