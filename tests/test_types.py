from datetime import date
from decimal import Decimal

from crossfoot.types import CrossfootResult, Txn


def test_txn_construction():
    t = Txn(date=date(2026, 9, 1), narration="UPI/DR/X", reference=None,
             debit=Decimal("100.00"), credit=None, balance=Decimal("900.00"), page=0)
    assert t.debit == Decimal("100.00")


def test_crossfoot_result_defaults():
    r = CrossfootResult(passed=True, checks=["a"], failed_checks=[])
    assert r.row_breaks == []
    assert r.rows_checked == 0
