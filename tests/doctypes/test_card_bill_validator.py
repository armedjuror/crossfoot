from datetime import date
from decimal import Decimal

from crossfoot.doctypes.credit_card_bill.validator import validate_card_bill
from crossfoot.types import CardTxn, ParsedCardBill


def _bill(txns, previous=Decimal("0.00"), total_due=None, section_totals=None, summary_only=Decimal("0.00")):
    return ParsedCardBill(
        currency="INR", locale="en-IN", previous_balance=previous, total_due=total_due,
        section_totals=section_totals or {}, summary_only_charges=summary_only,
        transactions=txns, masked_card="XXXX1234",
    )


def test_simple_purchase_reconciles():
    txns = [CardTxn(date(2026, 9, 1), "shop", Decimal("500.00"), "debit", "purchase", 0)]
    result = validate_card_bill(_bill(txns, total_due=Decimal("500.00")))
    assert result.passed


def test_refund_is_a_credit_in_any_section():
    txns = [
        CardTxn(date(2026, 9, 1), "shop", Decimal("500.00"), "debit", "purchase", 0),
        CardTxn(date(2026, 9, 2), "refund", Decimal("200.00"), "credit", "purchase", 0),
    ]
    result = validate_card_bill(_bill(txns, total_due=Decimal("300.00")))
    assert result.passed


def test_emi_conversion_reversal_plus_principal_and_interest():
    txns = [
        CardTxn(date(2026, 9, 1), "emi reversal", Decimal("1000.00"), "credit", "emi", 0),
        CardTxn(date(2026, 9, 2), "emi principal", Decimal("900.00"), "debit", "emi", 0),
        CardTxn(date(2026, 9, 2), "emi interest", Decimal("100.00"), "debit", "emi", 0),
    ]
    result = validate_card_bill(_bill(txns, total_due=Decimal("0.00")))
    assert result.passed


def test_gst_on_fee_as_tax_row():
    txns = [
        CardTxn(date(2026, 9, 1), "late fee", Decimal("500.00"), "debit", "fee", 0),
        CardTxn(date(2026, 9, 1), "gst on fee", Decimal("90.00"), "debit", "tax", 0),
    ]
    result = validate_card_bill(_bill(txns, total_due=Decimal("590.00")))
    assert result.passed


def test_section_total_mismatch_is_flagged():
    txns = [CardTxn(date(2026, 9, 1), "shop", Decimal("500.00"), "debit", "purchase", 0)]
    result = validate_card_bill(
        _bill(txns, total_due=Decimal("500.00"), section_totals={"purchase": Decimal("999.00")})
    )
    assert not result.passed
    assert "section_total:purchase" in result.failed_checks


def test_empty_transactions_reconciles():
    """Additional test: empty transactions list should pass trivially."""
    result = validate_card_bill(_bill([], previous=Decimal("100.00"), total_due=Decimal("100.00")))
    assert result.passed
    assert result.rows_checked == 0
