from datetime import date
from decimal import Decimal

from crossfoot.pipeline.extract import extract
from crossfoot.synth import generate_statement_pdf


def test_extract_returns_words_with_coordinates(tmp_path):
    out = tmp_path / "synth.pdf"
    generate_statement_pdf(
        str(out), opening=Decimal("1000.00"),
        rows=[(date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None)],
    )
    doc = extract(str(out))
    assert doc.page_count == 1
    assert doc.is_scanned is False
    words_text = {w.text for w in doc.pages[0]}
    assert "GROCERY" in " ".join(words_text) or any("GROCERY" in w for w in words_text)
    first_word = doc.pages[0][0]
    assert first_word.x1 > first_word.x0
    assert first_word.page == 0


def test_scanned_pdf_is_flagged(tmp_path):
    # A page with no extractable text at all simulates an image-only scan.
    import fitz
    out = tmp_path / "blank.pdf"
    d = fitz.open()
    d.new_page()
    d.save(str(out))
    doc = extract(str(out))
    assert doc.is_scanned is True
