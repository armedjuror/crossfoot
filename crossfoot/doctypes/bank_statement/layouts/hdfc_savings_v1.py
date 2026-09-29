from datetime import date as date_type

from crossfoot.countries.india import parse_amount, parse_date
from crossfoot.pipeline.tables import assign_column, band_rows
from crossfoot.types import ExtractedDoc, ParsedStatement, Txn

_COLUMNS = [
    ("date", 0.0), ("narration", 65.0), ("ref", 283.0),
    ("value_date", 350.0), ("debit", 405.0), ("credit", 491.0), ("balance", 564.0),
]
_HEADER_WORDS = {"Narration", "Withdrawal", "Deposit", "Closing"}

# Only the first page prints the column-header row (around y=231.5); on
# continuation pages the table body starts right after a repeating
# letterhead block that ends well above this line, so a single cutoff works
# for every page as long as it sits below the letterhead and at/above the
# first data row on continuation pages.
_TABLE_TOP_Y = 220.0


def _is_date_line(line) -> bool:
    dates = 0
    for w in line:
        try:
            parse_date(w.text)
            dates += 1
        except ValueError:
            continue
    return dates >= 1 and any(w.x0 < 60 for w in line)


class HdfcSavingsV1:
    slug = "hdfc_savings_v1"
    document_type = "bank_statement"
    country = "IN"

    def matches(self, doc: ExtractedDoc) -> float:
        if not doc.pages:
            return 0.0
        texts = {w.text for w in doc.pages[0]}
        hits = len(_HEADER_WORDS & texts)
        return hits / len(_HEADER_WORDS)

    def parse(self, doc: ExtractedDoc) -> ParsedStatement:
        txns: list[Txn] = []
        for page_index, words in enumerate(doc.pages):
            table_words = [w for w in words if w.y0 > _TABLE_TOP_Y]
            bands = band_rows(table_words, anchor=_is_date_line, y_tolerance=10.0)
            for band in bands:
                by_col: dict[str, list] = {}
                for w in band:
                    by_col.setdefault(assign_column(w, _COLUMNS), []).append(w)
                if "date" not in by_col:
                    continue
                txn_date = parse_date(sorted(by_col["date"], key=lambda w: w.x0)[0].text)
                narration = " ".join(w.text for w in sorted(by_col.get("narration", []), key=lambda w: w.x0))
                debit = None
                if "debit" in by_col:
                    debit_text = sorted(by_col["debit"], key=lambda w: w.x0)[0].text
                    if debit_text not in ("-", ""):
                        debit = parse_amount(debit_text)
                credit = None
                if "credit" in by_col:
                    credit_text = sorted(by_col["credit"], key=lambda w: w.x0)[0].text
                    if credit_text not in ("-", ""):
                        credit = parse_amount(credit_text)
                balance = None
                if "balance" in by_col:
                    balance = parse_amount(sorted(by_col["balance"], key=lambda w: w.x0)[0].text)
                txns.append(Txn(
                    date=txn_date, narration=narration or "", reference=None,
                    debit=debit, credit=credit, balance=balance, page=page_index,
                ))

        return ParsedStatement(
            currency="INR", locale="en-IN",
            period_from=txns[0].date if txns else date_type.today(),
            period_to=txns[-1].date if txns else date_type.today(),
            opening_balance=None, closing_balance=txns[-1].balance if txns else None,
            brought_forward={}, transactions=txns, masked_account=None,
        )
