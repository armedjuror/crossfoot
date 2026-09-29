from datetime import date
from decimal import Decimal

import pytest

from crossfoot.countries.india import parse_amount, parse_date


@pytest.mark.parametrize("text, expected", [
    ("1,234.00", Decimal("1234.00")),
    ("1,23,456.78", Decimal("123456.78")),   # Indian lakh grouping
    ("1,234.00 Dr", Decimal("-1234.00")),
    ("1,234.00 Cr", Decimal("1234.00")),
    ("4,353.42CR", Decimal("4353.42")),       # no space before suffix
    ("(500.00)", Decimal("-500.00")),         # bracketed negative
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_parse_amount_rejects_garbage():
    with pytest.raises(ValueError):
        parse_amount("not a number")


@pytest.mark.parametrize("text, expected", [
    ("01/09/2026", date(2026, 9, 1)),   # DD/MM/YYYY (SBI style)
    ("03/06/26", date(2026, 6, 3)),      # DD/MM/YY two-digit year (HDFC style)
    ("01-Sep-2026", date(2026, 9, 1)),   # DD-MMM-YYYY (IDFC style)
    ("2026-09-01", date(2026, 9, 1)),    # ISO (IDFC statement-period style)
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError):
        parse_date("not a date")
