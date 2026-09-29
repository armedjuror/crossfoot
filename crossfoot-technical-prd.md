# Crossfoot: Technical PRD

**Owner:** Ajwad Juman
**Status:** Draft v2 (2026-09-29). Revised after an independent review of v1.
**Audience:** whoever builds Crossfoot (you, or Claude Code working from this document).
**Companion doc:** `crossfoot-product-prd.md` (market, gate, pricing, GTM). This document covers architecture, validators, data model, and build order.

Crossfoot turns financial PDFs (bank statements, credit card bills, later tax invoices) into structured JSON. Every response carries a `crossfoot` block and an outcome that says whether the parse is verified, whether the document itself doesn't add up, or whether we couldn't read it.

Design properties:

1. **Deterministic first.** Rule-based layouts handle known formats at near-zero cost. An LLM is a later fallback, never the default (except invoices).
2. **Every output is validated.** Nothing is trusted until it passes the same deterministic validator.
3. **Two kinds of failure are kept apart.** "We misread it" (`parse_failed`, not billed) is never confused with "the document doesn't add up" (`document_inconsistent`, billed).
4. **Country-neutral core.** Every document has a `currency` and `locale`; country rules are plugins. India first, UAE next.
5. **Built for tenants, run by one person.** `tenant_id` from day one; manual key issuance in phase 0b; self-serve later.

---

## 1. Goals

- **Phase 0:** a local playground: upload, parse, correct in a UI, keep everything, measure parsers against a held-out set.
- **Phase 0b:** a public demo on `crossfoot.dev`: ephemeral, sandboxed, rate-limited upload returning JSON, CSV, or Excel; a waitlist; manually issued, prepaid API keys.
- Parse digital-PDF bank statements and card bills with no LLM on the common path.
- Turn every correction into a regression test.
- Add countries by adding layouts and locale rules, not by changing the core.
- Avoid retrofitting `tenant_id` later. (Other migrations will still happen; that's normal.)

---

## 2. Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Your stack; PDF tooling is Python-first |
| Web framework | FastAPI | Decided; the same app serves the demo and the API |
| Playground UI | Jinja2 templates + small vanilla JS | Decided; no frontend build step |
| ORM and migrations | SQLAlchemy 2 + Alembic | Standard, explicit, works well with Postgres |
| Tests | pytest | Standard |
| PDF extraction | PyMuPDF | Words with coordinates, page renders, true text redaction |
| Synthetic test PDFs | reportlab | Lets Claude Code build and test without real documents |
| OCR | Not in phase 0/0b | Count scans first |
| Database | PostgreSQL (local in phase 0; a free-tier managed Postgres that doesn't expire or delete data in phase 0b) | Verify the free tier's expiry and sleep rules at build time. The waitlist must survive the whole signal window. |
| Hosting (0b) | A free-tier container host, at least 512 MB RAM, kept warm with a free uptime pinger | Verify limits at build time. Cold starts during an announcement lose visitors. |
| Output formats | JSON; CSV (standard library); Excel (openpyxl) | Demo requirement |
| Money | `Decimal` only, quantized to the currency's minor unit | Floats cause the exact errors this product exists to catch |
| LLM fallback | Provider-agnostic interface, phase 1+ | Decided |

---

## 3. Core types

These are the contracts every component uses. Claude Code implements them first.

```python
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

@dataclass(frozen=True)
class Word:
    text: str
    x0: float; y0: float; x1: float; y1: float
    page: int

@dataclass
class ExtractedDoc:
    pages: list[list[Word]]          # words per page, in reading order
    page_count: int
    is_scanned: bool
    first_page_text: str

@dataclass
class LayoutMatch:
    slug: str                        # "hdfc_savings_v1"
    score: float                     # 0.0-1.0
    trusted: bool                    # passed the held-out gate (see §9)

@dataclass
class Txn:
    date: date
    narration: str
    reference: str | None
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None          # None when the layout prints no balance for this row
    page: int

@dataclass
class ParsedStatement:
    currency: str                    # ISO 4217: "INR", "AED"
    locale: str                      # "en-IN", "en-AE"
    period_from: date
    period_to: date
    opening_balance: Decimal | None  # None when not printed
    closing_balance: Decimal | None
    brought_forward: dict[int, Decimal]   # page number -> B/F balance printed on that page
    transactions: list[Txn]
    masked_account: str | None       # last 4 digits only

@dataclass
class CardTxn:
    date: date
    description: str
    amount: Decimal                  # always positive
    direction: Literal["debit", "credit"]
    section: str                     # "purchase", "payment", "fee", "interest", "tax", "emi", "refund", ...
    page: int

@dataclass
class ParsedCardBill:
    currency: str
    locale: str
    previous_balance: Decimal        # signed: negative if the card was in credit
    total_due: Decimal               # signed: negative if the bill shows a credit balance
    section_totals: dict[str, Decimal]   # only sections this layout maps; signed (debits +, credits -)
    summary_only_charges: Decimal    # charges in the summary with no transaction row; usually 0
    transactions: list[CardTxn]
    masked_card: str | None

@dataclass
class RowBreak:
    row_index: int
    expected: Decimal
    printed: Decimal

@dataclass
class CrossfootResult:
    passed: bool
    checks: list[str]                # every check that ran
    failed_checks: list[str]
    row_breaks: list[RowBreak] = field(default_factory=list)
    unchecked_rows: list[int] = field(default_factory=list)   # rows with no printed balance
    shape_errors: list[int] = field(default_factory=list)
    opening_balance_derived: bool = False
    rows_checked: int = 0

Outcome = Literal["verified", "document_inconsistent", "parse_failed",
                  "unsupported", "scanned", "bad_password", "error", "needs_review"]
```

---

## 4. Plugins

### 4.1 Document types

```python
class DocumentType(Protocol):
    slug: str   # "bank_statement" | "credit_card_bill" | "invoice"
    def validate(self, parsed) -> CrossfootResult: ...
```

### 4.2 Layouts

```python
class Layout(Protocol):
    slug: str
    document_type: str
    country: str                        # "IN", "AE"
    def matches(self, doc: ExtractedDoc) -> float: ...
    def parse(self, doc: ExtractedDoc) -> ParsedStatement | ParsedCardBill: ...
```

Phase 0 layouts are hand-written Python modules. From phase 1, the template learning loop can produce declarative layouts from the database; both implement `Layout`.

### 4.3 Country rules

```python
class CountryRules(Protocol):
    code: str                            # "IN", "AE"
    currency: str
    def parse_amount(self, text: str) -> Decimal: ...     # handles grouping, Dr/Cr, brackets
    def parse_date(self, text: str) -> date: ...
    def validate_invoice(self, parsed) -> CrossfootResult: ...   # phase 3
```

- **India:** grouping like `1,23,456.78`; `Dr`/`Cr` suffixes; dates mostly DD/MM/YYYY or DD-MMM-YYYY.
- **UAE:** grouping like `123,456.78`; statements are often bilingual English/Arabic, so layouts must read only the English columns and ignore Arabic text runs; dates usually DD/MM/YYYY. VAT invoices (15-digit TRN, 5% VAT) need their own validator later.

---

## 5. Pipeline

### 5.1 Ingestion
- Accept file, document type, and optional password.
- **Passwords are never stored or logged**, and are excluded from error responses (§13.4).
- Scanned detection: flag a page as image-only if it has very few words **or** if the share of valid printable characters is low (catches fonts with no text mapping). Scanned documents get outcome `scanned` and are not parsed in phase 0/0b.

### 5.2 Extraction
- PyMuPDF words with coordinates per page.
- Page renders (PNG) only in playground mode, for the correction UI. Hosted mode never renders or caches.

### 5.3 Layout classification
- Score every registered layout for the document type.
- Try every layout scoring above 0.5, highest first, and keep the first one whose parse passes crossfoot. If none passes, keep the highest-scoring layout's result.
- Log the top two scores. A small margin means two layouts are hard to tell apart (e.g. a bank's e-statement vs its net-banking download) and need better anchors.
- No layout above 0.5: outcome `unsupported`.

### 5.4 Table reconstruction
- **Column boundaries are found per page from the header words' x-positions**, not fixed per layout (column widths often change with content).
- **Row grouping uses the vertical span of each row's date and amount cells**, not "no date and no amount means continuation". That rule breaks when the date is vertically centred in a multi-line cell.
- Strip repeated page headers and footers.
- Keep brought-forward and carried-forward values in `ParsedStatement.brought_forward`, not as transactions.
- Card bills: attach foreign-currency conversion lines to the transaction above.
- Two date columns (transaction and value date): the layout declares which one is `date`.
- Rows printing `0.00` in both debit and credit: the layout treats `0.00` as empty.

### 5.5 Field parsing
- All amounts go through `CountryRules.parse_amount`, which returns a `Decimal` quantized to the currency's minor unit. `1,234.00 Dr` becomes `-1234.00` for balances.
- Account and card numbers are reduced to the last 4 digits in every output.

### 5.6 Validation (§6), then outcome (§7)

---

## 6. Validators

### 6.1 Bank statements (zero tolerance)

```python
ZERO = Decimal(0)

def _chain(txns, opening):
    running, breaks, unchecked = opening, [], []
    for i, t in enumerate(txns):
        expected = running + (t.credit or ZERO) - (t.debit or ZERO)
        if t.balance is None:                 # layout prints no balance on this row
            unchecked.append(i)
            running = expected
            continue
        if expected != t.balance:
            breaks.append(RowBreak(i, expected, t.balance))
        running = t.balance
    return running, breaks, unchecked

def _derive_opening(txns):
    t = txns[0]
    if t.balance is None:
        return None
    return t.balance - (t.credit or ZERO) + (t.debit or ZERO)

def choose_order(txns, opening):
    """Statements printed newest-first are reversed so the chain runs forward in time."""
    if len(txns) < 2:
        return txns
    if txns[0].date > txns[-1].date:
        return list(reversed(txns))
    if txns[0].date < txns[-1].date:
        return txns
    # All on one date: pick the direction with fewer breaks.
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
        failed.append("row_shape")                 # exactly one of debit/credit per row
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

    # The B/F balance on each page must equal the last balance on the previous page.
    # This is free and catches a dropped page.
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
```

Tested against a 3-row case with a missing balance, a derived opening balance, a newest-first ordering, and a one-rupee break; each behaved as expected.

Notes:
- **Zero tolerance.** Statements print exact figures; any tolerance would hide real errors.
- **What this proves:** amounts and balances are consistent on every row with a printed balance. It does not prove narrations are read correctly. Rows without a printed balance are listed in `unchecked_rows` and are not claimed as verified.

### 6.2 Credit card bills (zero tolerance)

```python
def signed(t: CardTxn) -> Decimal:
    return t.amount if t.direction == "debit" else -t.amount

def validate_card_bill(p: ParsedCardBill) -> CrossfootResult:
    checks = ["previous_plus_rows_equals_total_due", "section_totals", "dates_ordered"]
    failed = []

    # Primary check: every transaction row is covered.
    rows_net = sum((signed(t) for t in p.transactions), ZERO)
    if p.previous_balance + rows_net + p.summary_only_charges != p.total_due:
        failed.append("previous_plus_rows_equals_total_due")

    # Secondary: only sections this layout maps; totals are signed (debits +, credits -).
    for section, printed in p.section_totals.items():
        got = sum((signed(t) for t in p.transactions if t.section == section), ZERO)
        if got != printed:
            failed.append(f"section_total:{section}")

    if any(a.date > b.date for a, b in zip(p.transactions, p.transactions[1:])):
        failed.append("dates_ordered")

    return CrossfootResult(not failed, checks, failed, rows_checked=len(p.transactions))
```

Handles, by construction:
- GST on fees and interest appears as `tax` rows (or `summary_only_charges` if the issuer prints it only in the summary).
- EMI conversions appear as a credit (reversal) and debits (principal, interest).
- Refunds are credits, whatever section they're printed in.
- Issuers that print only combined "payments/credits" map one section; the check doesn't require separate fields.

**Known limitation:** a failure says the bill or a section is wrong, not which row.

### 6.3 Invoices (India, phase 3; validators usable standalone in phase 0)

```python
GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^(0[1-9]|[12][0-9]|3[0-8]|97|99)[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][0-9A-Z][0-9A-Z]$")
UT_WITHOUT_LEGISLATURE = {"04", "26", "31", "35", "38"}   # these use UTGST instead of SGST
INVOICE_TOLERANCE = Decimal("1.00")   # invoices only; see note below

def gstin_valid(g: str | None) -> bool:
    g = (g or "").strip().upper()
    if not GSTIN_RE.match(g):
        return False
    total = 0
    for i, c in enumerate(g[:14]):
        product = GSTIN_CHARS.index(c) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return GSTIN_CHARS[(36 - total % 36) % 36] == g[14]

def state_code(place_of_supply: str | None) -> str | None:
    m = re.match(r"\s*(\d{2})", place_of_supply or "")     # "29-Karnataka" -> "29"
    return m.group(1) if m else None                        # name-only forms map via a lookup table

def hsn_ok(h: str | None) -> bool:
    if not h:
        return True
    d = re.sub(r"[.\s]", "", h)                              # "8517.12" -> "851712"
    return d.isdigit() and len(d) in (2, 4, 6, 8)

def validate_invoice(p) -> CrossfootResult:
    failed = []
    if not gstin_valid(p.seller.gstin):
        failed.append("seller_gstin")
    buyer = (p.buyer.gstin or "").strip().upper()
    if buyer and buyer not in {"URP", "UNREGISTERED"} and not gstin_valid(buyer):
        failed.append("buyer_gstin")

    t = p.totals
    tax = t.cgst + t.sgst + t.utgst + t.igst
    if p.invoice_type == "bill_of_supply" or p.supply_category == "export_lut":
        if tax + t.cess != ZERO:
            failed.append("tax_on_untaxed_supply")
    else:
        seller_state = (p.seller.gstin or "")[:2]
        pos = state_code(p.place_of_supply)
        if pos is None:
            failed.append("place_of_supply_missing")
        else:
            intra = pos == seller_state and p.supply_category not in {"sez", "export_igst"}
            local = t.sgst + t.utgst
            if intra and (t.igst != ZERO or t.cgst != local):
                failed.append("tax_type")
            if not intra and (t.cgst != ZERO or local != ZERO):
                failed.append("tax_type")
            if intra and seller_state in UT_WITHOUT_LEGISLATURE and t.sgst != ZERO:
                failed.append("utgst_expected")

    collected = ZERO if p.reverse_charge else tax + t.cess
    if abs(t.taxable + collected - t.total) > INVOICE_TOLERANCE:
        failed.append("tax_equation")
    if abs(sum((li.taxable_value for li in p.line_items), ZERO) - t.taxable) > INVOICE_TOLERANCE:
        failed.append("line_items_sum")
    for li in p.line_items:
        if not hsn_ok(li.hsn):
            failed.append(f"hsn_row_{li.row_index}")

    checks = ["seller_gstin", "buyer_gstin", "tax_type", "tax_equation", "line_items_sum", "hsn"]
    return CrossfootResult(not failed, checks, failed, rows_checked=len(p.line_items))
```

Invoice fields added in this version: `utgst`, `cess`, `reverse_charge`, `supply_category` (`b2b`, `b2c`, `sez`, `export_igst`, `export_lut`), and `invoice_type` (`tax_invoice`, `bill_of_supply`, `credit_note`, `debit_note`, `proforma`).

Tested: the GSTIN check accepts known valid GSTINs (including lowercase with trailing spaces), rejects a changed check digit, and rejects the structurally invalid strings the reviewer found passing v1. An intra-state invoice, a same-state SEZ supply with IGST, and a reverse-charge invoice all pass; an intra-state invoice charged IGST fails with `tax_type`.

**Tolerance asymmetry is deliberate.** Invoices round per line item, so ₹1 tolerance applies only here. Statements and card bills use zero. Put a comment on both constants pointing to this section.

UAE VAT invoices get their own `CountryRules.validate_invoice` later.

---

## 7. Outcome: verified vs inconsistent vs failed

```python
MAX_LOCALIZED_BREAKS = 3

def decide_outcome(result: CrossfootResult, layout: LayoutMatch, structurally_clean: bool) -> Outcome:
    if result.passed:
        return "verified"
    if (layout.trusted
            and structurally_clean
            and not result.shape_errors
            and "dates_in_period" not in result.failed_checks
            and "dates_ordered" not in result.failed_checks
            and len(result.row_breaks) <= MAX_LOCALIZED_BREAKS):
        return "document_inconsistent"
    return "parse_failed"
```

- **`structurally_clean`** means: every amount matched the expected numeric format, every date parsed, no unassigned words inside the table area, and the row count per page matches the layout's expectations.
- **`MAX_LOCALIZED_BREAKS = 3`** (decided default). A document with many breaks is far more likely to be a misread than an edited statement.
- **`trusted`** layouts are those that passed the held-out technical gate (§9, product PRD §6.8). A new, untrusted layout can only produce `verified` or `parse_failed`.
- `document_inconsistent` responses include each break with the expected and printed values, so a human can check it in seconds.
- For card bills, `document_inconsistent` applies only to the primary check and is labelled lower confidence.
- **Billing:** `verified` and `document_inconsistent` consume credits. Everything else doesn't.
- **Wording in API output and UI:** "Totals don't reconcile at rows …". Never "tampered" or "fraud".

---

## 8. Output schema

Amounts are strings with exactly the currency's minor-unit decimals (`"11550.00"`, never `"11550.0"` or `"1E+3"`). Dates are ISO 8601.

```json
{
  "document_type": "bank_statement",
  "outcome": "verified",
  "currency": "INR",
  "issuer": "bank_a",
  "layout": "bank_a_savings_v1",
  "period": {"from": "2026-08-01", "to": "2026-08-31"},
  "account": {"masked_number": "XXXX1234"},
  "opening_balance": "12450.00",
  "closing_balance": "8210.55",
  "transactions": [
    {"date": "2026-08-03", "narration": "UPI/...", "reference": "1234567890",
     "debit": "450.00", "credit": null, "balance": "12000.00"}
  ],
  "crossfoot": {
    "passed": true,
    "checks": ["row_shape", "dates_in_period", "dates_ordered", "running_balance", "closing_balance", "page_continuity"],
    "failed_checks": [],
    "row_breaks": [],
    "unchecked_rows": [],
    "opening_balance_derived": false,
    "rows_checked": 142
  },
  "meta": {"pages": 4, "billed_pages": 4, "used_llm_fallback": false}
}
```

Card bills and invoices use the same envelope with their own body fields (§3, §6.3).

**CSV and Excel:** the transaction table plus a header block with `outcome` and failed checks. Any cell starting with `=`, `+`, `-`, or `@` is prefixed with `'` to prevent formula injection.

**Decimal hygiene:** one `parse_amount` per country; JSON from the browser (correction UI) is read with `parse_float=Decimal` or as strings; a lint rule bans `Decimal(<float>)`.

---

## 9. Held-out set, redaction, and regression

### 9.1 Held-out set
- Every document has `split` = `train` or `holdout`, set at upload and never changed.
- One month per account and card is `holdout`; every third-party or concierge sample is `holdout` by default.
- Held-out documents are hidden from the correction UI's default view, are never redacted for tool sharing, and are only used by `crossfoot regress --holdout`.
- A layout becomes `trusted` when it meets the product PRD's technical gate on its held-out documents.

### 9.2 Redaction for sharing with Claude
```
crossfoot redact DOCUMENT_ID --out redacted.pdf
```
- Uses PyMuPDF redaction annotations **applied** to the page, which removes the underlying text (not a drawn box).
- Removes: names, addresses, account and card numbers, IFSC, UPI IDs and VPAs, phone numbers, emails, and counterparty names inside narrations (pattern rules plus manual boxes you add).
- Keeps: amounts, balances, dates, and layout geometry.
- Afterwards, re-extracts the text and fails if any removed pattern still appears.
- Refuses to run on `holdout` documents.

### 9.3 Regression
```
crossfoot regress [--holdout]
```
- Reparses every stored document with the current code and compares against gold corrections.
- **Row alignment:** gold and parsed rows are matched on (date, amount, direction) using ordered matching, so an inserted or deleted row doesn't shift every later row.
- **Metrics per layout:** row precision and recall, field accuracy on matched rows (share of fields that equal gold), outcome counts, and for held-out runs the gate metrics.
- Also re-validates gold corrections under the current validator and reports any that no longer pass.
- `parser_version` is the git commit hash; if the working tree has uncommitted changes, the version ends in `-dirty`.

---

## 10. Data model

`tenant_id` on every tenant-owned table. Phase 0 uses one default tenant. The hosted demo never writes `documents` or `parses` rows; anonymous uploads write only `usage_events`.

```sql
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Used from phase 0b (manual issuance). Key format: cf_<8-char base32 id>_<32 random bytes, base32>.
CREATE TABLE api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    key_id        TEXT NOT NULL UNIQUE,      -- the 8-char id part, for lookup
    secret_hmac   TEXT NOT NULL,             -- HMAC-SHA256(server pepper, secret); compare with hmac.compare_digest
    daily_page_limit INT NOT NULL DEFAULT 200,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

-- Prepaid page credits. Balance = SUM(delta) per tenant.
CREATE TABLE page_credits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    delta       INT NOT NULL,                -- +100 for a pack, -N per billed parse
    reason      TEXT NOT NULL CHECK (reason IN ('pack_purchase','usage','adjustment','refund')),
    reference   TEXT,                        -- UPI or bank reference for manual payments
    amount_paid NUMERIC(12,2),
    currency    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON page_credits (tenant_id);

-- Playground only.
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    document_type   TEXT NOT NULL CHECK (document_type IN ('bank_statement','credit_card_bill','invoice')),
    country         TEXT NOT NULL,           -- 'IN', 'AE'
    currency        TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN ('train','holdout')),
    source          TEXT NOT NULL CHECK (source IN ('self','family_friend','concierge')),
    owner_label     TEXT NOT NULL,
    consent_note    TEXT,
    storage_path    TEXT,
    sha256          TEXT NOT NULL,
    page_count      INT,
    is_scanned      BOOLEAN NOT NULL DEFAULT false,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    UNIQUE (id, tenant_id),
    CHECK (source = 'self' OR consent_note IS NOT NULL)
);
CREATE UNIQUE INDEX ON documents (tenant_id, sha256) WHERE deleted_at IS NULL;

CREATE TABLE parses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL,
    document_id         UUID NOT NULL,
    parser_version      TEXT NOT NULL,
    layout_slug         TEXT,
    layout_score        NUMERIC,
    runner_up_score     NUMERIC,
    outcome             TEXT NOT NULL CHECK (outcome IN ('verified','document_inconsistent','parse_failed',
                            'unsupported','scanned','bad_password','error','needs_review')),
    crossfoot_passed    BOOLEAN,             -- NULL when validation didn't run
    failed_checks       TEXT[] NOT NULL DEFAULT '{}',
    used_llm_fallback   BOOLEAN NOT NULL DEFAULT false,
    output_json         JSONB,
    duration_ms         INT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id)
);
CREATE INDEX ON parses (document_id, created_at DESC);

CREATE TABLE corrections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL,
    document_id      UUID NOT NULL,
    base_parse_id    UUID REFERENCES parses(id),
    corrected_json   JSONB NOT NULL,
    crossfoot_passed BOOLEAN NOT NULL,
    is_gold          BOOLEAN NOT NULL DEFAULT false,
    edit_count       INT NOT NULL,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id),
    CHECK (NOT is_gold OR crossfoot_passed)
);
CREATE UNIQUE INDEX one_gold_per_document ON corrections (document_id) WHERE is_gold;
-- Promoting a new gold unsets the old one in the same transaction.

CREATE TABLE layout_templates (
    slug                 TEXT PRIMARY KEY,
    document_type        TEXT NOT NULL,
    country              TEXT NOT NULL,
    definition           JSONB NOT NULL,
    source_parse_id      UUID REFERENCES parses(id),
    validated_successes  INT NOT NULL DEFAULT 0,
    active               BOOLEAN NOT NULL DEFAULT false,
    trusted              BOOLEAN NOT NULL DEFAULT false,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE regression_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parser_version      TEXT NOT NULL,
    split               TEXT NOT NULL CHECK (split IN ('train','holdout','all')),
    documents_run       INT NOT NULL,
    per_layout          JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 0b (hosted). No document content is stored in any of these.
CREATE TABLE waitlist_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL,
    company         TEXT NOT NULL,
    use_case        TEXT NOT NULL,
    monthly_volume  TEXT NOT NULL CHECK (monthly_volume IN ('<1k','1k-10k','10k-50k','50k+')),
    is_company_email BOOLEAN NOT NULL,       -- false for gmail.com, outlook.com, etc.
    status          TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','contacted','offered','paid','declined')),
    tenant_id       UUID REFERENCES tenants(id),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON waitlist_requests (lower(email));

CREATE TABLE bank_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL,
    document_type   TEXT NOT NULL,
    requested_bank  TEXT NOT NULL,           -- typed by the user; never inferred from their file
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON bank_requests (lower(email), lower(requested_bank));

CREATE TABLE usage_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel         TEXT NOT NULL CHECK (channel IN ('demo_upload','api')),
    status          TEXT NOT NULL CHECK (status IN ('pending','done')),
    tenant_id       UUID REFERENCES tenants(id),
    api_key_id      UUID REFERENCES api_keys(id),
    ip_hash         TEXT,                    -- daily-rotating salt; cleared after 30 days
    document_type   TEXT NOT NULL,
    page_count      INT,
    country         TEXT,
    outcome         TEXT,
    billed_pages    INT NOT NULL DEFAULT 0,
    output_format   TEXT CHECK (output_format IN ('json','csv','xlsx')),
    duration_ms     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON usage_events (ip_hash, created_at);
CREATE INDEX ON usage_events (api_key_id, created_at);
```

Notes:
- **No `layout_slug` in `usage_events`.** Knowing which bank an anonymous uploader uses is more than the demo needs; `country` and `outcome` are enough.
- **Tenant consistency** is enforced by the composite foreign keys.
- **Credits:** a billed parse inserts a `usage` row with a negative delta in the same transaction as the response. A request is refused when the balance is below the document's page count.

---

## 11. LLM fallback (phase 1+)

```python
class LLMFallback(Protocol):
    def extract(self, doc: ExtractedDoc, document_type: str, hint: LayoutMatch | None): ...
```

- **Triggers:** unsupported or low-scoring layout, or `parse_failed`; always for invoices.
- **Never trusted directly.** The output goes through the same validator and outcome rules. LLM-parsed documents can be `verified` or `parse_failed`, **never `document_inconsistent`** (an LLM misread isn't evidence against the document).
- **Provider requirements:** zero data retention and no training on inputs, in writing.
- Track the fallback rate per layout from the first call.

---

## 12. Template learning loop (phase 1+)

1. The LLM fallback parses an unknown layout and the result is `verified`.
2. Derive a declarative template and store it with `active = false`.
3. Each further document parsed correctly with the template increments `validated_successes`.
4. At 3, `active = true` (the deterministic path takes over). `trusted` is set separately, only after held-out scoring.

---

## 13. Hosted mode (phase 0b and later)

### 13.1 Modes
- `CROSSFOOT_MODE=playground` (local) or `hosted`.
- Hosted mode refuses to start if a raw-storage path or page rendering is configured.

### 13.2 Routes

| Route | Purpose |
|---|---|
| `GET /` | Hero, supported banks and issuers (generated from trusted layouts), sample outputs, upload, waitlist link |
| `POST /demo/parse` | File, type, optional password, format (`json`, `csv`, `xlsx`) |
| `GET /samples/{slug}` | Pre-generated output from synthetic statements |
| `GET/POST /waitlist` | Access request form |
| `POST /bank-request` | Email + bank name, typed by the user |
| `POST /v1/parse` | API for issued keys (`Authorization: Bearer cf_...`); consumes credits |
| `GET /v1/credits` | Remaining page balance for the key's tenant |
| `GET /privacy` | Full retention notice |

### 13.3 Sandboxed parsing
- Every parse runs in a **separate process** from a `multiprocessing` pool with `maxtasksperchild=1`.
- The child sets `RLIMIT_AS` (400 MB) and `RLIMIT_CPU` (25 s). The parent kills it at 30 s.
- **One parse at a time** per instance; extra requests get a "busy, try again" response.
- Upload limits: 10 MB and 30 pages (raise after measuring on the real host).
- Uploads may be spooled to a temporary file by the web framework. That file is deleted in a `finally` block, and the privacy notice says temporary disk use happens.

### 13.4 Leak prevention
- Custom handler for request-validation errors that drops the echoed `input` (it could contain the password).
- No error reporter that captures local variables; tracebacks never include request bodies.
- Access logs contain method, path, status, and duration only. Check that the host's own proxy doesn't log request bodies.

### 13.5 Rate limiting
- Before parsing, insert a `usage_events` row with `status = 'pending'` inside a transaction that holds `pg_advisory_xact_lock(hashtext(ip_hash))`, and count that IP's rows for the day, including rejected, scanned, and oversize uploads. This blocks parallel-upload bypasses.
- Client IP comes only from the host's documented forwarding header, taking the last hop added by the host's proxy. Never trust a client-supplied header.
- IPv6 addresses are hashed at /64.
- Limits: 5 anonymous uploads per IP per day; a global cap of 300 anonymous uploads per day; 200 pages per day per API key (raise by hand on request; the request itself is a signal).
- Carrier-grade NAT (common on Indian mobile networks) means some users share an IP. The limit message includes the waitlist link.

### 13.6 Privacy notice (on the upload page)

> Your file is processed and deleted as soon as the result is returned. It may be written to temporary disk during processing. We never keep the file, its contents, or its password. We keep a record of each upload: document type, page count, country, whether it parsed, and a hashed version of your IP address (used for rate limits and deleted after 30 days). Only upload documents you own or have permission to share.

The waitlist and bank-request forms state that the email you enter is stored so we can contact you.

### 13.7 Waitlist, payment, and key issuance (CLI only)

```
crossfoot waitlist                      # list new requests
crossfoot offer WAITLIST_ID             # mark offered
crossfoot paid WAITLIST_ID --pages 100 --amount 999 --currency INR --ref UPI_REF
                                        # creates tenant + credits, marks paid
crossfoot issue-key TENANT_ID           # prints the key once
crossfoot revoke KEY_ID
crossfoot metrics                       # gate metrics for the signal window
```

- No web admin page. The CLI connects to the hosted database directly.
- Waitlist notifications: `crossfoot waitlist` checked daily, or email to yourself through your own account's SMTP (free). No paid email service before Go.
- `crossfoot metrics` reports only gate-relevant numbers (product PRD §6.8): company waitlist requests, keys used on 2+ days, conversations logged, payments, and demo uploads labelled as "does not count".

---

## 14. Playground (phase 0, local)

Runs with `uvicorn` bound to `127.0.0.1`. No auth.

| Page | Purpose |
|---|---|
| Upload | File, type, country, optional password, split (`train`/`holdout`), source, owner label, consent note (required unless `self`) |
| Documents | Filter by type, country, split, layout, outcome, has-gold. Held-out documents are hidden unless you explicitly show them. |
| Document detail | Page images on the left; editable rows and summary fields on the right; failing rows highlighted from `row_breaks` |
| Report | Latest `regress` results per layout, train vs holdout |

**Correction UI**
- Edit cells; insert, delete, merge, and split rows; edit summary fields.
- **Re-validate** runs the validator on the edited data without saving.
- **Save correction** stores it with `edit_count`. **Mark as gold** only when it passes.
- **Reparse** reruns the current parser and creates a new `parses` row.

**CLI (playground)**
```
crossfoot parse FILE --type bank_statement --country IN [--password-prompt]
crossfoot redact DOCUMENT_ID --out FILE.pdf
crossfoot regress [--holdout]
crossfoot fixture DOCUMENT_ID --out tests/fixtures/NAME.json
crossfoot synth --layout SLUG --months 3 --out tests/pdfs/
```
- `fixture` writes a scrubbed JSON with consistent balances (and optionally a deliberately broken copy).
- `synth` generates synthetic PDFs with reportlab that mimic a layout's structure, so Claude Code can build and test without real documents.

**Repo layout**
```
crossfoot/
  pyproject.toml
  alembic/
  crossfoot/
    app.py              # FastAPI app; playground or hosted mode
    cli.py
    config.py
    types.py            # §3
    db/models.py
    pipeline/           # ingest, extract, classify, tables
    countries/
      india.py          # parse_amount, parse_date, GST validators
      uae.py            # parse_amount, parse_date (VAT later)
    doctypes/
      bank_statement/   validator.py, layouts/
      credit_card_bill/ validator.py, layouts/
      invoice/          validator.py
    outcome.py          # §7
    hosted/             # sandbox, rate limit, routes (phase 0b)
    redact.py
    synth.py
    templates/  static/
  tests/
    pdfs/               # synthetic PDFs only
    fixtures/           # scrubbed JSON only
  data/                 # git-ignored: raw/, renders/
```

---

## 15. Data retention

**Playground (local): keep everything**, under `data/` (git-ignored, encrypted disk). Consent required for anything not yours. Deleting a document removes its files and sets `deleted_at`.

**Hosted (demo and API): ephemeral.** No `documents` or `parses` rows, no raw files, no renders, no output JSON. Only `usage_events` metadata, the waitlist, bank requests, keys, and credits. `ip_hash` is cleared after 30 days. Playground data is never copied to the hosted database.

Put a comment on the storage code pointing here, so neither mode gets "fixed" into the other.

---

## 16. Quality loop

- Outcome counts and `verified` rate per layout, train vs holdout.
- Held-out field accuracy vs gold (§9.3). A change that lowers it isn't committed.
- `document_inconsistent` rate per layout. A sudden rise on one layout usually means a parser bug or a bank format change, not a wave of edited statements; investigate before trusting it.
- Edit count per correction.
- Fallback rate per layout (phase 1+).
- Drift alerts on hosted pass rates (phase 2+).

No model grades another model's output.

---

## 17. Build order

### Phase 0: Playground (validation weeks 1-3)
1. Types (§3), country rules for India, schema (§10), synthetic PDF generator.
2. Extraction and table reconstruction, developed against synthetic PDFs.
3. Bank statement validator (§6.1) and outcome logic (§7) with tests.
4. Upload, documents, detail pages; correction UI; `regress`, `redact`, `fixture`.
5. Real bank layouts (3+ families), built by you from redacted training statements.
6. Card validator (§6.2) and 2-3 issuer layouts.
7. Invoice validators (§6.3) as tested standalone functions.
8. UAE country rules once samples arrive.

**Exit:** the product PRD's technical gate, scored with `crossfoot regress --holdout`.

### Phase 0b: Public demo (validation week 4, live weeks 5-7)
- Hosted mode (§13): sandbox, leak prevention, rate limits, privacy notice.
- Public page, demo upload with JSON/CSV/Excel, samples from synthetic statements, waitlist, bank requests.
- `/v1/parse`, `/v1/credits`, prepaid credits, CLI for waitlist, payment, keys, metrics.
- Deploy to a free-tier host and free-tier Postgres behind `crossfoot.dev`.
- **Exit:** the product PRD's market gate.

### Phase 1: Production bank statements (after Go)
8-10 Indian banks; UAE banks if samples and a buyer exist; LLM fallback (§11); template loop (§12); OCR if scans are a real share.

### Phase 2: Cards + open API
5-8 issuers; payment gateway; self-serve packs; docs; move to AWS when spend allows.

### Phase 3: Invoices
India GST invoices via LLM-first extraction plus §6.3; UAE VAT validator if UAE buyers exist.

---

## 18. Out of scope

| Item | Reason |
|---|---|
| Sharing held-out or third-party documents with any AI tool | Keeps the gate honest and consent intact |
| Web admin page | CLI only; avoids exposing the waitlist |
| Self-serve keys and a payment gateway before Go | Manual payments and key issuance during validation |
| OCR in phase 0/0b | Count scans first |
| Fraud or tamper claims | The check reports arithmetic, not intent |
| Countries beyond India and the UAE | Product decision |
| Matching transactions across documents | Different product |

---

## 19. Instruction for Claude Code (phase 0)

> Build the Crossfoot phase-0 playground from `crossfoot-technical-prd.md`. Use Python 3.12, FastAPI with Jinja2 and minimal vanilla JS, SQLAlchemy 2 with Alembic, pytest, PyMuPDF, reportlab, and PostgreSQL. Implement the types in §3 exactly, the India country rules in §4.3, the schema in §10 (playground tables plus `tenants` and `api_keys`, with one default tenant), and the pipeline in §5 for digital PDFs. Implement the validators in §6.1 and §6.2 and the outcome logic in §7 exactly as written, using Decimal only, with pytest cases for missing balances, newest-first statements, derived opening balances, page-continuity breaks, and card bills with refunds, EMI, and tax rows. Implement the invoice validators in §6.3 as standalone tested functions. Build `crossfoot synth` first and develop every layout and test against synthetic PDFs. **You will not receive real statements; the owner adds real layout modules from redacted training statements.** Build the playground pages, correction UI, and CLI in §14, the redaction tool in §9.2, and regression in §9.3. Store everything per the playground rules in §15; never store or log passwords; bind to 127.0.0.1. Do not build hosted mode (§13), the LLM fallback, or the template loop.
