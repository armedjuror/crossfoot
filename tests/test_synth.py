from datetime import date
from decimal import Decimal

import fitz

from crossfoot.synth import generate_statement_pdf


def test_generated_pdf_contains_expected_words(tmp_path):
    out = tmp_path / "synth.pdf"
    rows = [
        (date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None),
        (date(2026, 9, 2), "UPI/CR/SALARY", None, Decimal("5000.00")),
    ]
    generate_statement_pdf(str(out), opening=Decimal("1000.00"), rows=rows)

    doc = fitz.open(str(out))
    text = doc[0].get_text()
    assert "UPI/DR/GROCERY" in text
    assert "900.00" in text     # 1000 - 100
    assert "5,900.00" in text   # 900 + 5000, grouped
