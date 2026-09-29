from datetime import date as date_type

from crossfoot.countries.india import parse_amount, parse_date
from crossfoot.pipeline.tables import band_rows
from crossfoot.types import ExtractedDoc, ParsedStatement, Txn, Word

_PARTICULARS_MAX_X, _DEBIT_X, _CREDIT_X, _BALANCE_X = 374.0, 374.0, 452.0, 527.0
_HEADER_WORDS = {"Transaction", "Particulars", "Cheque", "Balance"}

# A registered-office footer line ("IDFC FIRST BANK LIMITED, ...") repeats on
# every real content page. Real other-bank files (HDFC, SBI x3) already in
# this repo were checked empirically and contain zero occurrences of "IDFC"
# or "FIRST" anywhere, so this is a strong bank-name signal -- but, following
# the lesson from the SBI layout (Task 13), it must be a contiguous same-line
# phrase match, not scattered set-membership, since scattered tokens could in
# principle appear individually elsewhere (e.g. in an unrelated narration).
# Gap threshold measured on the real IDFC footer line's own word geometry
# (~2pt between consecutive words); the threshold used is well above that but
# still far below the width of an unrelated word or narration segment.
_BANK_NAME_PHRASE = ("idfc", "first", "bank")
_BANK_NAME_LINE_TOLERANCE = 2.0
_BANK_NAME_MAX_GAP = 15.0

# The brief's own naive header-word check (fraction of _HEADER_WORDS present,
# checked only on page 0, no positional constraint) was found to score 0.5 on
# two of the three real SBI files in this repo -- their netbanking "Relationship
# Summary" cover pages happen to carry generic "Transaction" and "Balance"
# words, right at the registry's classification threshold. Gating on the
# bank-name phrase above closes that false-positive risk the same way Task 13
# closed its analogous one.
_SUMMARY_LABEL_MAX_GAP_Y = 25.0


def _is_anchor_line(line) -> bool:
    date_count = sum(1 for w in line if w.x0 < _ANCHOR_DATE_MAX_X and _looks_like_date(w.text))
    return date_count >= 2


# A "STATEMENT PERIOD : <date> TO <date>" line near the top of every real
# content page also carries two parseable dates on the same physical line,
# but those dates sit well to the right of where the real transaction-date
# and value-date columns start (measured on the real document: transaction/
# value date columns both start at x0 < 120; the period line's dates start at
# x0 > 165). Restrict the anchor test to dates left of this cutoff so that
# line is correctly excluded and absorbed as page furniture instead of
# producing a spurious all-None row -- mirrors the same guard the SBI layout
# uses for its own analogous period line.
_ANCHOR_DATE_MAX_X = 150.0


def _looks_like_date(text: str) -> bool:
    try:
        parse_date(text)
        return True
    except ValueError:
        return False


def _is_reference_noise(text: str) -> bool:
    # The redaction tool's synthetic replacement reference numbers: bare
    # 12+ digit strings with no separators, distinct from amounts (which
    # always carry a decimal point -- and typically comma grouping -- in
    # this layout, so they never satisfy str.isdigit()).
    return text.isdigit() and len(text) >= 10


def _lines(words: list[Word]) -> list[list[Word]]:
    grouped: dict[float, list[Word]] = {}
    for w in words:
        key = round(w.y0 * 2) / 2
        grouped.setdefault(key, []).append(w)
    return [grouped[y] for y in sorted(grouped)]


def _extract_summary_balances(doc: ExtractedDoc):
    """IDFC prints its own structured summary row ("Opening Balance" / "Total
    Debit" / "Total Credit" / "Closing Balance") near the top of every real
    content page (identical values repeated each page -- a fixed
    statement-period header, not a per-page rolling total). Locate it by
    label adjacency (not hardcoded pixel constants) and read the four amounts
    directly below it, bucketed by x-position against the label boundaries.

    Returns (opening_balance, closing_balance), or (None, None) if the
    summary row cannot be located (graceful degradation -- callers fall back
    to deriving the opening balance from the first transaction).
    """
    for words in doc.pages:
        lines = _lines(words)
        for i, line in enumerate(lines):
            texts = {w.text for w in line}
            if not ({"Opening", "Closing"} <= texts and "Total" in texts):
                continue
            totals = sorted((w for w in line if w.text == "Total"), key=lambda w: w.x0)
            closing_labels = [w for w in line if w.text == "Closing"]
            if len(totals) < 2 or not closing_labels:
                continue
            debit_boundary = totals[0].x0
            label_y = line[0].y0

            for other in lines[i + 1:]:
                gap = other[0].y0 - label_y
                if gap <= 0 or gap > _SUMMARY_LABEL_MAX_GAP_Y:
                    continue
                amounts = []
                for w in sorted(other, key=lambda w: w.x0):
                    try:
                        amounts.append(parse_amount(w.text))
                    except ValueError:
                        continue
                if len(amounts) >= 4:
                    return amounts[0], amounts[-1]
                break
    return None, None


def _has_bank_name_phrase(words: list[Word]) -> bool:
    starts = [w for w in words if w.text.lower() == _BANK_NAME_PHRASE[0]]
    for start in starts:
        same_line = sorted(
            (w for w in words if abs(w.y0 - start.y0) <= _BANK_NAME_LINE_TOLERANCE and w.x0 >= start.x0),
            key=lambda w: w.x0,
        )
        prev = start
        matched = 1
        for token in _BANK_NAME_PHRASE[1:]:
            nxt = next(
                (w for w in same_line if w.text.lower() == token and w.x0 > prev.x0),
                None,
            )
            if nxt is None or (nxt.x0 - prev.x1) > _BANK_NAME_MAX_GAP:
                break
            prev = nxt
            matched += 1
        if matched == len(_BANK_NAME_PHRASE):
            return True
    return False


class IdfcSavingsV1:
    slug = "idfc_savings_v1"
    document_type = "bank_statement"
    country = "IN"

    def matches(self, doc: ExtractedDoc) -> float:
        if not doc.pages:
            return 0.0

        has_bank_name = any(_has_bank_name_phrase(words) for words in doc.pages)
        if not has_bank_name:
            return 0.0

        texts = {w.text for w in doc.pages[0]}
        hits = len(_HEADER_WORDS & texts)
        score = 0.4 + 0.6 * (hits / len(_HEADER_WORDS))
        return min(score, 1.0)

    def parse(self, doc: ExtractedDoc) -> ParsedStatement:
        txns: list[Txn] = []
        for page_index, words in enumerate(doc.pages):
            table_words = [w for w in words if not _is_reference_noise(w.text)]
            bands = band_rows(table_words, anchor=_is_anchor_line, y_tolerance=12.0)
            for band in bands:
                dated = sorted((w for w in band if _looks_like_date(w.text)), key=lambda w: w.x0)
                if len(dated) < 2:
                    continue
                txn_date = parse_date(dated[0].text)

                narration_words = sorted(
                    (w for w in band if w.x0 < _PARTICULARS_MAX_X and w not in dated),
                    key=lambda w: (w.y0, w.x0),
                )
                narration = " ".join(w.text for w in narration_words)

                debit = None
                for w in band:
                    if _DEBIT_X <= w.x0 < _CREDIT_X:
                        try:
                            debit = parse_amount(w.text)
                        except ValueError:
                            continue
                        break
                credit = None
                for w in band:
                    if _CREDIT_X <= w.x0 < _BALANCE_X:
                        try:
                            credit = parse_amount(w.text)
                        except ValueError:
                            continue
                        break
                balance = None
                for w in band:
                    if w.x0 >= _BALANCE_X:
                        try:
                            balance = parse_amount(w.text)
                        except ValueError:
                            continue
                        break

                txns.append(Txn(
                    date=txn_date, narration=narration, reference=None,
                    debit=debit, credit=credit, balance=balance, page=page_index,
                ))

        opening_balance, closing_balance = _extract_summary_balances(doc)
        if closing_balance is None:
            closing_balance = txns[-1].balance if txns else None

        return ParsedStatement(
            currency="INR", locale="en-IN",
            period_from=txns[0].date if txns else date_type.today(),
            period_to=txns[-1].date if txns else date_type.today(),
            opening_balance=opening_balance, closing_balance=closing_balance,
            brought_forward={}, transactions=txns, masked_account=None,
        )
