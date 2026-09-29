from decimal import Decimal

from crossfoot.types import CardTxn, CrossfootResult, ParsedCardBill

ZERO = Decimal(0)


def signed(t: CardTxn) -> Decimal:
    return t.amount if t.direction == "debit" else -t.amount


def validate_card_bill(p: ParsedCardBill) -> CrossfootResult:
    checks = ["previous_plus_rows_equals_total_due", "section_totals", "dates_ordered"]
    failed = []

    rows_net = sum((signed(t) for t in p.transactions), ZERO)
    if p.previous_balance + rows_net + p.summary_only_charges != p.total_due:
        failed.append("previous_plus_rows_equals_total_due")

    for section, printed in p.section_totals.items():
        got = sum((signed(t) for t in p.transactions if t.section == section), ZERO)
        if got != printed:
            failed.append(f"section_total:{section}")

    if any(a.date > b.date for a, b in zip(p.transactions, p.transactions[1:])):
        failed.append("dates_ordered")

    return CrossfootResult(not failed, checks, failed, rows_checked=len(p.transactions))
