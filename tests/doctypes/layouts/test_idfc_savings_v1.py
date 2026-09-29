from decimal import Decimal

from crossfoot.doctypes.bank_statement.layouts.idfc_savings_v1 import IdfcSavingsV1
from crossfoot.types import ExtractedDoc, Word

_HEADER = [
    ("Transaction", 47.0, 460.9), ("Cheque", 304.5, 460.9),
    ("Value", 128.1, 466.4), ("Date", 151.2, 466.4), ("Particulars", 219.6, 466.4),
    ("Debit", 374.9, 466.4), ("Credit", 452.2, 466.4), ("Balance", 527.2, 466.4),
    ("Date", 60.8, 471.9), ("No", 313.8, 471.9),
]

# Real IDFC statements print a "REGISTERED OFFICE: IDFC FIRST BANK LIMITED, ..."
# footer line on every content page; matches() requires this contiguous
# bank-name phrase (not just the generic header words, which alone were found
# to false-positive against another real bank's cover page -- see the
# regression test below) so it doesn't claim a differently-banked document.
_BANK_NAME = [("IDFC", 133.9, 749.5), ("FIRST", 154.8, 749.5), ("BANK", 180.0, 749.5)]


def _header_words():
    words = [Word(t, x, y, x + len(t) * 5, y + 9, 0) for t, x, y in _HEADER]
    words += [Word(t, x, y, x + len(t) * 5, y + 9, 0) for t, x, y in _BANK_NAME]
    return words


def _row(y, txn_date, prefix, mid, suffix, debit, credit, balance):
    words = [
        Word(prefix, 192.7, y - 11.0, 192.7 + len(prefix) * 5, y - 2.0, 0),
        Word(txn_date, 35.0, y, 35.0 + 60, y + 9, 0),
        Word(txn_date, 113.9, y, 113.9 + 60, y + 9, 0),
        Word(mid, 223.7, y, 223.7 + len(mid) * 5, y + 9, 0),
    ]
    if debit is not None:
        words.append(Word(debit, 383.7, y, 423.7, y + 9, 0))
    if credit is not None:
        words.append(Word(credit, 467.0, y, 507.0, y + 9, 0))
    words.append(Word(balance, 541.4, y, 581.4, y + 9, 0))
    words.append(Word(suffix, 192.7, y + 11.0, 192.7 + len(suffix) * 5, y + 20.0, 0))
    return words


def test_matches_scores_high_on_real_header():
    doc = ExtractedDoc(pages=[_header_words()], page_count=1, is_scanned=False,
                        first_page_text="STATEMENT OF ACCOUNT")
    assert IdfcSavingsV1().matches(doc) >= 0.6


def test_matches_scores_zero_without_bank_name_even_with_all_header_words():
    # A same-shaped document from a different Indian bank can also carry
    # generic table-header words like "Transaction" and "Balance" (found
    # empirically against a real control file's netbanking cover page) --
    # those alone must not be enough to claim the document as IDFC.
    words = [Word(t, x, y, x + len(t) * 5, y + 9, 0) for t, x, y in _HEADER]
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False,
                        first_page_text="STATEMENT OF ACCOUNT")
    assert IdfcSavingsV1().matches(doc) == 0.0


def test_parse_debit_and_credit_rows_with_three_line_narration():
    words = _header_words()
    words += _row(519.3, "01-Sep-2026", "UPI/DR/", "/KKBK/", "Example Merchant", "51,507.96", None, "50,997.04")
    words += _row(556.9, "03-Sep-2026", "UPI/CR/", "/SBIN/", "Example Payer", None, "2,000.00", "52,997.04")
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    parsed = IdfcSavingsV1().parse(doc)
    assert len(parsed.transactions) == 2
    assert parsed.transactions[0].debit == Decimal("51507.96")
    assert "Example Merchant" in parsed.transactions[0].narration
    assert parsed.transactions[1].credit == Decimal("2000.00")
    assert parsed.transactions[1].balance == Decimal("52997.04")


def test_parse_reads_opening_and_closing_balance_from_printed_summary_row():
    words = _header_words()
    words += [
        Word("Opening", 66.3, 388.7, 98.8, 396.7, 0), Word("Balance", 101.0, 388.7, 131.7, 396.7, 0),
        Word("Total", 216.6, 388.7, 235.1, 396.7, 0), Word("Debit", 237.4, 388.7, 257.4, 396.7, 0),
        Word("Total", 353.1, 388.7, 371.6, 396.7, 0), Word("Credit", 373.8, 388.7, 396.9, 396.7, 0),
        Word("Closing", 481.9, 388.7, 511.2, 396.7, 0), Word("Balance", 513.4, 388.7, 544.1, 396.7, 0),
        Word("10,000.00", 120.7, 404.4, 163.0, 412.4, 0),
        Word("5,000.00", 258.7, 404.4, 301.0, 412.4, 0),
        Word("2,000.00", 396.7, 404.4, 439.0, 412.4, 0),
        Word("7,000.00", 561.4, 404.4, 577.0, 412.4, 0),
    ]
    words += _row(519.3, "01-Sep-2026", "UPI/DR/", "/KKBK/", "Example Merchant", "1,000.00", None, "9,000.00")
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    parsed = IdfcSavingsV1().parse(doc)
    assert parsed.opening_balance == Decimal("10000.00")
    assert parsed.closing_balance == Decimal("7000.00")


def test_reference_noise_digit_string_is_excluded_from_narration_and_amounts():
    words = _header_words()
    words += _row(519.3, "01-Sep-2026", "UPI/DR/", "/KKBK/", "Example Merchant", "1,000.00", None, "9,000.00")
    # A synthetic 12-digit replacement reference number sitting on the
    # prefix line, right after the UPI transaction-type code -- must not be
    # swept into the narration or misread as an amount.
    words.append(Word("232374238704", 222.1, 508.4, 260.5, 516.3, 0))
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    parsed = IdfcSavingsV1().parse(doc)
    assert len(parsed.transactions) == 1
    assert "232374238704" not in parsed.transactions[0].narration
    assert parsed.transactions[0].debit == Decimal("1000.00")
