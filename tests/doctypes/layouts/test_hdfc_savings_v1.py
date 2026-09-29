from decimal import Decimal

from crossfoot.doctypes.bank_statement.layouts.hdfc_savings_v1 import HdfcSavingsV1


def _make_words(rows):
    # rows: list of (date_str, narration, debit_str, deposit_str, balance_str)
    from crossfoot.types import Word
    words = []
    # First data row sits ~17.6pt below the header on the real statement's
    # first page; keep that gap here so the header line (absorbed by nothing,
    # since it's > y_tolerance away from the first anchor) doesn't collide
    # with the first transaction band.
    y = 249.1
    header = [
        ("Date", 39.9), ("Narration", 144.2), ("Chq./Ref.No.", 283.5),
        ("Value", 361.5), ("Dt", 383.5), ("Withdrawal", 405.3), ("Amt.", 448.7),
        ("Deposit", 491.1), ("Amt.", 518.8), ("Closing", 564.3), ("Balance", 592.1),
    ]
    for text, x in header:
        words.append(Word(text, x, 231.5, x + len(text) * 5, 240.5, 0))
    for date_s, narration, debit_s, deposit_s, balance_s in rows:
        words.append(Word(date_s, 33.7, y, 33.7 + 50, y + 9, 0))
        words.append(Word(narration, 72.0, y, 72.0 + len(narration) * 5, y + 9, 0))
        words.append(Word(date_s, 362.5, y, 362.5 + 50, y + 9, 0))   # value date, same as txn date
        if debit_s:
            words.append(Word(debit_s, 448.2, y, 448.2 + 40, y + 9, 0))
        if deposit_s:
            words.append(Word(deposit_s, 518.0, y, 518.0 + 40, y + 9, 0))
        words.append(Word(balance_s, 598.7, y, 598.7 + 40, y + 9, 0))
        y += 20
    return words


def test_matches_scores_high_on_real_header():
    from crossfoot.types import ExtractedDoc
    doc = ExtractedDoc(pages=[_make_words([("03/06/26", "AMB CHRG", "354.00", None, "3,646.00")])],
                        page_count=1, is_scanned=False, first_page_text="")
    layout = HdfcSavingsV1()
    assert layout.matches(doc) >= 0.8


def test_parse_two_rows_reconciles():
    from crossfoot.types import ExtractedDoc
    rows = [
        ("03/06/26", "AMB CHRG INCL GST", "354.00", None, "3,646.00"),
        ("05/06/26", "UPI CREDIT", None, "100.00", "3,746.00"),
    ]
    doc = ExtractedDoc(pages=[_make_words(rows)], page_count=1, is_scanned=False, first_page_text="")
    parsed = HdfcSavingsV1().parse(doc)
    assert len(parsed.transactions) == 2
    assert parsed.transactions[0].debit == Decimal("354.00")
    assert parsed.transactions[0].credit is None
    assert parsed.transactions[1].credit == Decimal("100.00")
    assert parsed.transactions[1].balance == Decimal("3746.00")
