from decimal import Decimal

from crossfoot.doctypes.bank_statement.layouts.sbi_savings_v1 import SbiSavingsV1
from crossfoot.types import ExtractedDoc, Word


def _row(y, txn_date, txn_type, narration, ref_dash, debit, credit, balance):
    words = [
        Word(txn_type, 138.0, y - 7.2, 138.0 + len(txn_type) * 5, y - 0.2, 0),
        Word(txn_date, 27.5, y, 27.5 + 50, y + 9, 0),
        Word(txn_date, 82.5, y, 82.5 + 50, y + 9, 0),
        Word(ref_dash, 303.7, y, 305.7, y + 9, 0),
    ]
    if debit is not None:
        words.append(Word(debit, 351.9, y, 391.9, y + 9, 0))
        words.append(Word("-", 446.2, y, 448.2, y + 9, 0))
    else:
        words.append(Word("-", 366.2, y, 368.2, y + 9, 0))
        words.append(Word(credit, 429.7, y, 469.7, y + 9, 0))
    words.append(Word(balance, 512.2, y, 552.2, y + 9, 0))
    words.append(Word(narration, 138.0, y + 2.2, 138.0 + len(narration) * 5, y + 11.2, 0))
    return words


def test_matches_scores_high_when_balance_and_upi_narration_present():
    # Real SBI e-statements also print "State Bank of India" as running
    # stationery text on the statement page; matches() requires this bank-name
    # signal (not just the generic "STATEMENT OF ACCOUNT" caption or "Balance"
    # header word, both of which other Indian banks' statements can also
    # carry) so it doesn't claim a differently-banked document. Include that
    # stationery text here so the fixture mimics the real page.
    words = _row(536.2, "01/09/2026", "WDL TF", "UPI/DR//GROCERY", "-", "1,563.00", None, "23,334.37")
    words = words + [
        Word("State", 250.0, 30.0, 280.0, 39.0, 0),
        Word("Bank", 285.0, 30.0, 310.0, 39.0, 0),
        Word("of", 315.0, 30.0, 325.0, 39.0, 0),
        Word("India", 330.0, 30.0, 360.0, 39.0, 0),
    ]
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    assert SbiSavingsV1().matches(doc) >= 0.6


def test_matches_scores_zero_without_bank_name_even_with_statement_and_balance_and_upi():
    # A same-shaped document from a different Indian bank can also print
    # "STATEMENT OF ACCOUNT", a "Balance" header word, and UPI-prefixed
    # narrations -- those alone must not be enough to claim it as SBI.
    words = _row(536.2, "01/09/2026", "WDL TF", "UPI/DR//GROCERY", "-", "1,563.00", None, "23,334.37")
    words = words + [Word("Balance", 512.2, 20.0, 552.2, 29.0, 0)]
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    assert SbiSavingsV1().matches(doc) == 0.0


def test_parse_debit_and_credit_rows_with_three_line_grouping():
    words = (
        _row(536.2, "01/09/2026", "WDL TF", "UPI/DR//GROCERY", "-", "1,563.00", None, "23,334.37")
        + _row(586.2, "01/09/2026", "DEP TF", "UPI/CR//SALARY", "-", None, "10,000.00", "33,334.37")
    )
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    parsed = SbiSavingsV1().parse(doc)
    assert len(parsed.transactions) == 2
    assert parsed.transactions[0].debit == Decimal("1563.00")
    assert parsed.transactions[0].credit is None
    assert "GROCERY" in parsed.transactions[0].narration
    assert parsed.transactions[1].credit == Decimal("10000.00")
    assert parsed.transactions[1].balance == Decimal("33334.37")
