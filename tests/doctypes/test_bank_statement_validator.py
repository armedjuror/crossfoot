from datetime import date
from decimal import Decimal

from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
from crossfoot.types import ParsedStatement, Txn


def _stmt(txns, opening=Decimal("1000.00"), closing=None, brought_forward=None):
    return ParsedStatement(
        currency="INR", locale="en-IN",
        period_from=date(2026, 9, 1), period_to=date(2026, 9, 30),
        opening_balance=opening, closing_balance=closing,
        brought_forward=brought_forward or {}, transactions=txns, masked_account="XXXX1234",
    )


def test_clean_chain_verifies():
    txns = [
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), None, Decimal("900.00"), 0),
        Txn(date(2026, 9, 2), "b", None, None, Decimal("50.00"), Decimal("950.00"), 0),
    ]
    result = validate_bank_statement(_stmt(txns, closing=Decimal("950.00")))
    assert result.passed
    assert result.row_breaks == []


def test_missing_balance_is_unchecked_not_a_break():
    txns = [
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), None, None, 0),
        Txn(date(2026, 9, 2), "b", None, None, Decimal("50.00"), Decimal("950.00"), 0),
    ]
    result = validate_bank_statement(_stmt(txns, closing=Decimal("950.00")))
    assert result.passed
    assert result.unchecked_rows == [0]


def test_newest_first_statement_is_reordered():
    txns = [
        Txn(date(2026, 9, 2), "b", None, None, Decimal("50.00"), Decimal("950.00"), 0),
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), None, Decimal("900.00"), 0),
    ]
    result = validate_bank_statement(_stmt(txns, closing=Decimal("950.00")))
    assert result.passed


def test_derived_opening_balance_when_none_printed():
    txns = [
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), None, Decimal("900.00"), 0),
    ]
    result = validate_bank_statement(_stmt(txns, opening=None))
    assert result.passed
    assert result.opening_balance_derived


def test_one_rupee_break_is_flagged():
    txns = [
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), None, Decimal("899.99"), 0),
    ]
    result = validate_bank_statement(_stmt(txns))
    assert not result.passed
    assert "running_balance" in result.failed_checks
    assert result.row_breaks == [__import__("crossfoot.types", fromlist=["RowBreak"]).RowBreak(
        0, Decimal("900.00"), Decimal("899.99"))]


def test_page_continuity_break():
    txns = [
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), None, Decimal("900.00"), 0),
        Txn(date(2026, 9, 2), "b", None, None, Decimal("50.00"), Decimal("950.00"), 1),
    ]
    result = validate_bank_statement(
        _stmt(txns, closing=Decimal("950.00"), brought_forward={1: Decimal("999.00")})
    )
    assert "page_continuity" in result.failed_checks


def test_empty_transactions_with_no_opening_balance_does_not_crash():
    result = validate_bank_statement(_stmt([], opening=None))
    assert not result.passed
    assert "no_opening_balance" in result.failed_checks


def test_row_shape_failure_survives_no_opening_balance_early_return():
    txns = [
        Txn(date(2026, 9, 1), "a", None, Decimal("100.00"), Decimal("50.00"), None, 0),
    ]
    result = validate_bank_statement(_stmt(txns, opening=None))
    assert not result.passed
    assert "row_shape" in result.failed_checks
    assert "no_opening_balance" in result.failed_checks
    assert result.shape_errors == [0]
