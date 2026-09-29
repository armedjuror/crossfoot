from datetime import date as date_type

from crossfoot.countries.india import parse_amount, parse_date
from crossfoot.pipeline.tables import band_rows
from crossfoot.types import ExtractedDoc, ParsedStatement, Txn

# Column x-ranges measured from the real SBI e-statement (header row has no
# extractable text; only "Balance" survives, so these are hand-anchored).
_REF_X, _DEBIT_X, _CREDIT_X, _BALANCE_X = 290.0, 340.0, 400.0, 480.0

# Real SBI netbanking exports sometimes prepend a "Relationship Summary" /
# "Account Summary" dashboard page before the actual statement page, so the
# "STATEMENT OF ACCOUNT" caption is not always on page 0 (doc.first_page_text)
# -- it can be one page later. Checking the tokenized words across every page
# catches that case. The bank name is also required: "STATEMENT OF ACCOUNT",
# a "Balance" column header word, and "UPI/..." narrations are all generic
# enough that another Indian bank's e-statement can carry every one of them,
# so requiring "State"/"Bank"/"India" print tokens (which appear on SBI's own
# statement page but not on other banks' statements) is what keeps this
# layout from also claiming a differently-banked document.
_BANK_NAME_TOKENS = {"State", "Bank", "India"}


# A handful of non-transaction lines (a "Statement From ... to ..." period
# line near the header, and a repeat of it in a footer/summary block) also
# carry two parseable dates on the same physical line, but those dates sit
# well to the right of where the real transaction-date and value-date
# columns start. Restrict the anchor test to dates left of this cutoff so
# those lines are correctly excluded and absorbed as page furniture instead
# of producing spurious all-None rows.
_ANCHOR_DATE_MAX_X = 150.0


def _is_anchor_line(line) -> bool:
    date_count = 0
    for w in line:
        if w.x0 >= _ANCHOR_DATE_MAX_X:
            continue
        try:
            parse_date(w.text)
            date_count += 1
        except ValueError:
            continue
    return date_count >= 2


def _amount_in_range(band, x_start, x_end):
    for w in band:
        if x_start <= w.x0 < x_end and w.text != "-":
            try:
                return parse_amount(w.text)
            except ValueError:
                continue
    return None


class SbiSavingsV1:
    slug = "sbi_savings_v1"
    document_type = "bank_statement"
    country = "IN"

    def matches(self, doc: ExtractedDoc) -> float:
        has_statement_phrase = "STATEMENT OF ACCOUNT" in doc.first_page_text
        has_bank_name = False
        has_balance = False
        has_upi = False
        for words in doc.pages:
            texts = {w.text for w in words}
            if not has_statement_phrase and {"STATEMENT", "ACCOUNT"} <= texts:
                has_statement_phrase = True
            if _BANK_NAME_TOKENS <= texts:
                has_bank_name = True
            if "Balance" in texts:
                has_balance = True
            if any("UPI/" in w.text for w in words):
                has_upi = True

        if not has_statement_phrase or not has_bank_name:
            return 0.0
        score = 0.4
        if has_balance:
            score += 0.3
        if has_upi:
            score += 0.3
        return min(score, 1.0)

    def parse(self, doc: ExtractedDoc) -> ParsedStatement:
        txns: list[Txn] = []
        for page_index, words in enumerate(doc.pages):
            bands = band_rows(words, anchor=_is_anchor_line, y_tolerance=10.0)
            for band in bands:
                dated_words = []
                for w in band:
                    try:
                        parse_date(w.text)
                        dated_words.append(w)
                    except ValueError:
                        continue
                if len(dated_words) < 2:
                    continue
                dated_words.sort(key=lambda w: w.x0)
                txn_date = parse_date(dated_words[0].text)
                anchor_y = dated_words[0].y0

                # The anchor (date/amount) line sits between a type-code line
                # above it and a narration-continuation line below it. Narration
                # words live in the left column (x < _REF_X) on both the anchor
                # line itself and the lines immediately above/below it; sort by
                # y then x so the type-code line's words precede the anchor
                # line's, which precede the continuation line's.
                narration_words = sorted(
                    (w for w in band if w.x0 < _REF_X and w not in dated_words),
                    key=lambda w: (w.y0, w.x0),
                )
                narration = " ".join(w.text for w in narration_words)

                debit = _amount_in_range(band, _DEBIT_X, _CREDIT_X)
                credit = _amount_in_range(band, _CREDIT_X, _BALANCE_X)
                balance = None
                for w in sorted(band, key=lambda w: abs(w.y0 - anchor_y)):
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

        return ParsedStatement(
            currency="INR", locale="en-IN",
            period_from=txns[0].date if txns else date_type.today(),
            period_to=txns[-1].date if txns else date_type.today(),
            opening_balance=None, closing_balance=txns[-1].balance if txns else None,
            brought_forward={}, transactions=txns, masked_account=None,
        )
