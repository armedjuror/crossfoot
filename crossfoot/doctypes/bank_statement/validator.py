from decimal import Decimal

from crossfoot.types import CrossfootResult, ParsedStatement, RowBreak, Txn

ZERO = Decimal(0)


def _chain(txns: list[Txn], opening: Decimal):
    running, breaks, unchecked = opening, [], []
    for i, t in enumerate(txns):
        expected = running + (t.credit or ZERO) - (t.debit or ZERO)
        if t.balance is None:
            unchecked.append(i)
            running = expected
            continue
        if expected != t.balance:
            breaks.append(RowBreak(i, expected, t.balance))
        running = t.balance
    return running, breaks, unchecked


def _derive_opening(txns: list[Txn]):
    t = txns[0]
    if t.balance is None:
        return None
    return t.balance - (t.credit or ZERO) + (t.debit or ZERO)


def choose_order(txns: list[Txn], opening: Decimal | None):
    """Statements printed newest-first are reversed so the chain runs forward in time."""
    if len(txns) < 2:
        return txns
    if txns[0].date > txns[-1].date:
        return list(reversed(txns))
    if txns[0].date < txns[-1].date:
        return txns
    fwd = _chain(txns, opening if opening is not None else _derive_opening(txns))[1]
    rev_txns = list(reversed(txns))
    rev = _chain(rev_txns, opening if opening is not None else _derive_opening(rev_txns))[1]
    return rev_txns if len(rev) < len(fwd) else txns


def validate_bank_statement(p: ParsedStatement) -> CrossfootResult:
    checks = ["row_shape", "dates_in_period", "dates_ordered",
              "running_balance", "closing_balance", "page_continuity"]
    failed = []
    txns = choose_order(p.transactions, p.opening_balance)

    shape = [i for i, t in enumerate(txns) if (t.debit is None) == (t.credit is None)]
    if shape:
        failed.append("row_shape")
    if any(not (p.period_from <= t.date <= p.period_to) for t in txns):
        failed.append("dates_in_period")
    if any(a.date > b.date for a, b in zip(txns, txns[1:])):
        failed.append("dates_ordered")

    opening, derived = p.opening_balance, False
    if opening is None:
        opening, derived = _derive_opening(txns), True
    if opening is None:
        return CrossfootResult(False, checks, ["no_opening_balance"], rows_checked=len(txns))

    running, breaks, unchecked = _chain(txns, opening)
    if breaks:
        failed.append("running_balance")
    if p.closing_balance is not None and running != p.closing_balance:
        failed.append("closing_balance")

    last_on_page = {}
    for t in txns:
        if t.balance is not None:
            last_on_page[t.page] = t.balance
    for page, bf in p.brought_forward.items():
        prev = last_on_page.get(page - 1)
        if prev is not None and prev != bf:
            failed.append("page_continuity")
            break

    return CrossfootResult(not failed, checks, failed, breaks, unchecked, shape, derived, len(txns))
