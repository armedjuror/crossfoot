import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

_AMOUNT_RE = re.compile(
    r"^(?:Rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(Dr|DR|Cr|CR)?$"
)
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y")
_QUANT = Decimal("0.01")


def parse_amount(text: str) -> Decimal:
    """Indian amount formats: '1,23,456.78', '1,234.00 Dr', '4,353.42CR', '(500.00)'."""
    t = text.strip()
    negative = t.startswith("(") and t.endswith(")")
    if negative:
        t = t[1:-1].strip()
    match = _AMOUNT_RE.match(t)
    if not match:
        raise ValueError(f"cannot parse amount: {text!r}")
    digits, suffix = match.groups()
    value = Decimal(digits.replace(",", ""))
    if negative or (suffix and suffix.upper() == "DR"):
        value = -value
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def parse_date(text: str) -> date:
    """Handles DD/MM/YYYY, DD/MM/YY, DD-MMM-YYYY, and ISO YYYY-MM-DD."""
    t = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"cannot parse date: {text!r}")
