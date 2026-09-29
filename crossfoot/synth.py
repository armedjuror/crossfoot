from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

_COLUMNS = [("Date", 40), ("Narration", 120), ("Debit", 340), ("Credit", 420), ("Balance", 500)]


def _group(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    s = f"{abs(amount):,.2f}"
    # Indian grouping: last 3 digits, then groups of 2.
    int_part, dec_part = s.replace(",", "").split(".")
    if len(int_part) <= 3:
        grouped = int_part
    else:
        last3, rest = int_part[-3:], int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        grouped = ",".join(groups) + "," + last3
    return f"{sign}{grouped}.{dec_part}"


def generate_statement_pdf(out_path: str, opening: Decimal, rows: list[tuple], layout: str = "generic_v1"):
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    y = height - 60
    c.setFont("Helvetica-Bold", 10)
    for label, x in _COLUMNS:
        c.drawString(x, y, label)
    y -= 20
    c.setFont("Helvetica", 9)

    running = opening
    for txn_date, narration, debit, credit in rows:
        running = running + (credit or Decimal("0")) - (debit or Decimal("0"))
        c.drawString(_COLUMNS[0][1], y, txn_date.strftime("%d/%m/%Y"))
        c.drawString(_COLUMNS[1][1], y, narration)
        if debit is not None:
            c.drawString(_COLUMNS[2][1], y, _group(debit))
        if credit is not None:
            c.drawString(_COLUMNS[3][1], y, _group(credit))
        c.drawString(_COLUMNS[4][1], y, _group(running))
        y -= 18
        if y < 60:
            c.showPage()
            y = height - 60
    c.save()
