from crossfoot.pipeline.tables import assign_column, band_rows
from crossfoot.types import Word


def _w(text, x0, y0, x1=None):
    return Word(text=text, x0=x0, y0=y0, x1=x1 or x0 + len(text) * 5, y1=y0 + 10, page=0)


def test_assign_column_picks_nearest_range():
    columns = [("date", 0.0), ("narration", 100.0), ("amount", 400.0)]
    assert assign_column(_w("01/09/2026", 10.0, 0), columns) == "date"
    assert assign_column(_w("UPI/DR/X", 150.0, 0), columns) == "narration"
    assert assign_column(_w("100.00", 420.0, 0), columns) == "amount"


def test_band_rows_single_line_per_row():
    # Two independent rows, each on its own line, no continuation lines.
    words = [
        _w("01/09/2026", 10, 100), _w("narration-a", 150, 100), _w("100.00", 420, 100),
        _w("02/09/2026", 10, 120), _w("narration-b", 150, 120), _w("200.00", 420, 120),
    ]
    has_date = lambda line: any(w.text.count("/") == 2 for w in line)
    rows = band_rows(words, anchor=has_date, y_tolerance=5.0)
    assert len(rows) == 2
    assert {w.text for w in rows[0]} == {"01/09/2026", "narration-a", "100.00"}


def test_band_rows_absorbs_narration_lines_above_and_below_the_anchor_line():
    # Mirrors the real SBI/IDFC layouts: a type/prefix line above the anchor
    # line, and a narration-continuation line below it, both within tolerance.
    words = [
        _w("WDL", 138, 93),          # line above (type code)
        _w("01/09/2026", 10, 100), _w("01/09/2026", 60, 100),
        _w("100.00", 420, 100), _w("900.00", 480, 100),   # anchor line
        _w("/GROCERY", 138, 102),    # line below (narration continuation)
        _w("02/09/2026", 10, 120), _w("02/09/2026", 60, 120),
        _w("200.00", 420, 120), _w("1100.00", 480, 120),
    ]
    has_date = lambda line: sum(1 for w in line if w.text.count("/") == 2) >= 2
    rows = band_rows(words, anchor=has_date, y_tolerance=8.0)
    assert len(rows) == 2
    row0_text = {w.text for w in rows[0]}
    assert "WDL" in row0_text and "/GROCERY" in row0_text and "100.00" in row0_text
