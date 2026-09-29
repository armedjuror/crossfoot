# Crossfoot Phase 0-A (Local Playground) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Crossfoot local playground (Phase 0-A): a FastAPI app that parses digital-PDF Indian bank statements (SBI, HDFC, IDFC layouts, built from real redacted training statements already in `data/`), validates every parse with a zero-tolerance running-balance check, and lets the owner correct and regress results — no hosted mode, no LLM fallback, no invoices, no card bills (none were provided).

**Architecture:** Deterministic rule-based pipeline: PyMuPDF word+coordinate extraction → per-layout table reconstruction (column boundaries from header/data geometry, row banding from date+amount vertical spans, since these three real statements each wrap narration across 2-3 physical text lines per transaction) → India country rules for amount/date parsing → a bank-statement validator that chains the running balance → an outcome decision (`verified` / `document_inconsistent` / `parse_failed`) → SQLAlchemy/Postgres storage for documents, parses, and corrections → a minimal Jinja2 playground UI and CLI.

**Tech Stack:** Python (3.14 installed; targeting the 3.12+ language features actually used — no 3.13+-only syntax), FastAPI + Jinja2 + vanilla JS, SQLAlchemy 2 + Alembic, PostgreSQL (local, already running via Homebrew), PyMuPDF (`pymupdf`/`fitz`), reportlab, pytest, argparse-based CLI (`crossfoot` console script).

**Spec:** `crossfoot-technical-prd.md` (architecture, types §3, country rules §4.3, pipeline §5, validators §6, outcome §7, schema §10, playground §14, redaction §9.2, regression §9.3, build order §17, Claude instructions §19) and `crossfoot-product-prd.md` §6.3–§6.6 (scope/timeline context only — no product-PRD code to implement).

## Global Constraints

- **Decimal only.** Every amount is `Decimal`, quantized to 2 places (INR minor unit). Never construct `Decimal` from a `float`. (Tech PRD §2, §5.5, §8)
- **Zero tolerance** in the bank statement validator — no epsilon, no rounding slack. (§6.1)
- **Passwords are never stored or logged**, and never echoed in error responses. (§5.1, §13.4 principle applies to playground too)
- **Playground binds to `127.0.0.1` only**, no auth. (§14)
- **`tenant_id` on every tenant-owned table**, one default tenant seeded at migration time. (§10)
- **Real documents live under `data/`, which is git-ignored.** Never commit a real PDF, a real extracted word dump, or real transaction narrations into a test fixture, docstring, commit message, or this plan's own code. Only synthetic (reportlab-generated) data and hand-written illustrative examples go into the repo/tests. (§15, and this session's redaction-verification finding: the SBI files still carry a residual PIN code, so treat `data/` contents as sensitive even though the owner cleared them for local use.)
- **No hosted mode, no LLM fallback, no invoice parser, no card-bill layouts.** None of the five provided PDFs is a credit card bill (confirmed this session: all five are savings-account statements from SBI, HDFC, IDFC). (§19, this session's PDF inspection)
- **No held-out split exists yet** for SBI, HDFC, or IDFC — the owner decided this session to treat all provided files (3 SBI, 1 HDFC, 1 IDFC) as training data. Every `documents` row this plan creates must still have `split = 'train'` (never `'holdout'`) so the schema stays honest, and the exit criteria below record this as a gap, not a pass.

## Review Focus

- **Empty cells rendered as a literal `-`** (seen in real SBI and IDFC rows) must become `None`, not be passed into `parse_amount` (which would raise). A layout that mis-handles this silently mis-books every row.
- **Two-digit years** (`03/06/26` in the real HDFC statement) must parse to 2026, not 1926 or crash `parse_date`.
- **Balance suffix with no space** (`4,353.42CR`, seen on the real SBI statement) must parse the same as `4,353.42 CR`.
- **Narration spanning multiple physical PDF text lines per transaction** (confirmed on both SBI and IDFC — see Milestone 4) must be reassembled into one row, not split into phantom extra transactions or dropped.
- **A transaction row whose amount matches a layout's own fake/placeholder reference number pattern** (12-digit digit-strings appear near IDFC rows — these are the redaction tool's synthetic replacement reference numbers, not amounts) must never be misread as a debit/credit figure.

---

## Milestone 1: Scaffolding, Core Types, Country Rules, Schema

### Task 1: Project scaffolding and local Postgres database

**Files:**
- Create: `pyproject.toml`
- Create: `crossfoot/__init__.py`
- Create: `crossfoot/config.py`
- Modify: `main.py` (delete — superseded by `crossfoot/app.py` in Task 16; keep it working until then by leaving it alone, do not delete yet)
- Create: `.gitignore`
- Create: `tests/__init__.py`

**Interfaces:**
- Produces: `crossfoot.config.Settings` — a small dataclass/pydantic-less config object with `database_url: str` (env var `CROSSFOOT_DATABASE_URL`, default `postgresql+psycopg://localhost/crossfoot`) and `mode: str` (env var `CROSSFOOT_MODE`, default `"playground"`).

- [ ] **Step 1: Initialize git and `.gitignore`**

```bash
git init
```

```gitignore
# .gitignore
venv/
__pycache__/
*.pyc
.pytest_cache/
data/
*.egg-info/
.DS_Store
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "crossfoot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn",
    "jinja2",
    "python-multipart",
    "sqlalchemy>=2.0",
    "alembic",
    "psycopg[binary]",
    "pymupdf",
    "reportlab",
    "openpyxl",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.scripts]
crossfoot = "crossfoot.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["crossfoot*"]
```

- [ ] **Step 3: Install into the existing venv and smoke-test on the installed Python version**

```bash
source venv/bin/activate
pip install -e ".[dev]"
python -c "import fastapi, sqlalchemy, alembic, psycopg, pymupdf, reportlab, openpyxl, pytest; print('ok')"
```

Expected: `ok`. If any import fails (most likely `psycopg` or a version pin conflict on the installed Python), resolve the specific package's install docs for that Python version before continuing — do not silently drop a dependency.

- [ ] **Step 4: Create the local database**

```bash
createdb crossfoot
psql crossfoot -c "select 1;"
```

Expected: `1` row returned. (Postgres was confirmed already running locally this session via `pg_isready`.)

- [ ] **Step 5: Write `crossfoot/config.py`**

```python
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    mode: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot"
        ),
        mode=os.environ.get("CROSSFOOT_MODE", "playground"),
    )
```

- [ ] **Step 6: Write a smoke test**

```python
# tests/test_config.py
from crossfoot.config import get_settings


def test_default_settings():
    settings = get_settings()
    assert settings.mode == "playground"
    assert "crossfoot" in settings.database_url
```

- [ ] **Step 7: Run the test**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml crossfoot/ tests/ .gitignore
git commit -m "chore: project scaffolding, config, local database"
```

---

### Task 2: Core types (spec §3, verbatim)

**Files:**
- Create: `crossfoot/types.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Produces: `Word`, `ExtractedDoc`, `LayoutMatch`, `Txn`, `ParsedStatement`, `CardTxn`, `ParsedCardBill`, `RowBreak`, `CrossfootResult`, `Outcome` — used by every later task verbatim from the spec.

- [ ] **Step 1: Write `crossfoot/types.py` exactly per Technical PRD §3**

```python
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


@dataclass
class ExtractedDoc:
    pages: list[list[Word]]
    page_count: int
    is_scanned: bool
    first_page_text: str


@dataclass
class LayoutMatch:
    slug: str
    score: float
    trusted: bool


@dataclass
class Txn:
    date: date
    narration: str
    reference: str | None
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None
    page: int


@dataclass
class ParsedStatement:
    currency: str
    locale: str
    period_from: date
    period_to: date
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    brought_forward: dict[int, Decimal]
    transactions: list[Txn]
    masked_account: str | None


@dataclass
class CardTxn:
    date: date
    description: str
    amount: Decimal
    direction: Literal["debit", "credit"]
    section: str
    page: int


@dataclass
class ParsedCardBill:
    currency: str
    locale: str
    previous_balance: Decimal
    total_due: Decimal
    section_totals: dict[str, Decimal]
    summary_only_charges: Decimal
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
    checks: list[str]
    failed_checks: list[str]
    row_breaks: list[RowBreak] = field(default_factory=list)
    unchecked_rows: list[int] = field(default_factory=list)
    shape_errors: list[int] = field(default_factory=list)
    opening_balance_derived: bool = False
    rows_checked: int = 0


Outcome = Literal[
    "verified", "document_inconsistent", "parse_failed",
    "unsupported", "scanned", "bad_password", "error", "needs_review",
]
```

- [ ] **Step 2: Write a construction smoke test**

```python
# tests/test_types.py
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_types.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add crossfoot/types.py tests/test_types.py
git commit -m "feat: core types from technical PRD section 3"
```

---

### Task 3: India country rules (spec §4.3)

**Files:**
- Create: `crossfoot/countries/__init__.py`
- Create: `crossfoot/countries/india.py`
- Test: `tests/countries/test_india.py`
- Test: `tests/countries/__init__.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `crossfoot.countries.india.parse_amount(text: str) -> Decimal`, `parse_date(text: str) -> date`. Every layout task (Milestone 4) calls these; no layout writes its own amount/date regex.

- [ ] **Step 1: Write the failing tests, covering the real formats already confirmed in the three provided statements** (values below are hand-written illustrative examples, not copied from the real files)

```python
# tests/countries/test_india.py
from datetime import date
from decimal import Decimal

import pytest

from crossfoot.countries.india import parse_amount, parse_date


@pytest.mark.parametrize("text, expected", [
    ("1,234.00", Decimal("1234.00")),
    ("1,23,456.78", Decimal("123456.78")),   # Indian lakh grouping
    ("1,234.00 Dr", Decimal("-1234.00")),
    ("1,234.00 Cr", Decimal("1234.00")),
    ("4,353.42CR", Decimal("4353.42")),       # no space before suffix
    ("(500.00)", Decimal("-500.00")),         # bracketed negative
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_parse_amount_rejects_garbage():
    with pytest.raises(ValueError):
        parse_amount("not a number")


@pytest.mark.parametrize("text, expected", [
    ("01/09/2026", date(2026, 9, 1)),   # DD/MM/YYYY (SBI style)
    ("03/06/26", date(2026, 6, 3)),      # DD/MM/YY two-digit year (HDFC style)
    ("01-Sep-2026", date(2026, 9, 1)),   # DD-MMM-YYYY (IDFC style)
    ("2026-09-01", date(2026, 9, 1)),    # ISO (IDFC statement-period style)
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError):
        parse_date("not a date")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/countries/test_india.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crossfoot.countries'`

- [ ] **Step 3: Implement**

```python
# crossfoot/countries/india.py
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

_AMOUNT_RE = re.compile(
    r"^(?:Rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(Dr|DR|Cr|CR)?$"
)
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y")
_QUANT = Decimal("0.01")


def parse_amount(text: str) -> Decimal:
    """Indian amount formats: '1,23,456.78', '1,234.00 Dr', '4,353.42CR', '(500.00)'."""
    t = text.strip()
    negative = t.startswith("(") and t.endswith(")")
    if negative:
        t = t[1:-1].strip()
    match = _AMOUNT_RE.match(t)
    if not match:
        raise ValueError(f"cannot parse amount: {text!r}")
    digits, suffix = match.groups()
    value = Decimal(digits.replace(",", ""))
    if negative or (suffix and suffix.upper() == "DR"):
        value = -value
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def parse_date(text: str) -> date:
    """Handles DD/MM/YYYY, DD/MM/YY, DD-MMM-YYYY, and ISO YYYY-MM-DD."""
    t = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"cannot parse date: {text!r}")
```

```python
# crossfoot/countries/__init__.py
```

```python
# tests/countries/__init__.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/countries/test_india.py -v`
Expected: PASS (all parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add crossfoot/countries/ tests/countries/
git commit -m "feat: India country rules for amount and date parsing"
```

---

### Task 4: Database schema and Alembic migration

**Files:**
- Create: `crossfoot/db/__init__.py`
- Create: `crossfoot/db/models.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_initial.py`
- Test: `tests/db/test_models.py`
- Test: `tests/db/__init__.py`

**Interfaces:**
- Produces: SQLAlchemy 2 declarative models `Tenant`, `Document`, `Parse`, `Correction`, `LayoutTemplate`, `RegressionRun`, and `crossfoot.db.models.Base`, `crossfoot.db.get_engine(url: str)`. Milestone 5's playground routes and CLI import these directly.
- Scope note: only the Phase-0 (non-hosted) tables from Technical PRD §10 are created here — `tenants`, `documents`, `parses`, `corrections`, `layout_templates`, `regression_runs`. `api_keys`, `page_credits`, `waitlist_requests`, `bank_requests`, and `usage_events` are explicitly "phase 0b (hosted)" per §10's own comments and Global Constraints above (no hosted mode this plan) — they belong to the Phase 0-B plan, not this one.

- [ ] **Step 1: Write the SQLAlchemy models**

```python
# crossfoot/db/models.py
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, ForeignKey, ForeignKeyConstraint, Index,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('bank_statement','credit_card_bill','invoice')",
            name="documents_document_type_check",
        ),
        CheckConstraint("split IN ('train','holdout')", name="documents_split_check"),
        CheckConstraint(
            "source IN ('self','family_friend','concierge')", name="documents_source_check"
        ),
        CheckConstraint(
            "source = 'self' OR consent_note IS NOT NULL", name="documents_consent_check"
        ),
        UniqueConstraint("id", "tenant_id", name="documents_id_tenant_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    document_type: Mapped[str]
    country: Mapped[str]
    currency: Mapped[str]
    split: Mapped[str]
    source: Mapped[str]
    owner_label: Mapped[str]
    consent_note: Mapped[str | None]
    storage_path: Mapped[str | None]
    sha256: Mapped[str]
    page_count: Mapped[int | None]
    is_scanned: Mapped[bool] = mapped_column(default=False)
    uploaded_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    deleted_at: Mapped[datetime | None]


Index(
    "documents_tenant_sha256_idx", Document.tenant_id, Document.sha256,
    unique=True, postgresql_where=Document.deleted_at.is_(None),
)


class Parse(Base):
    __tablename__ = "parses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"]
        ),
        CheckConstraint(
            "outcome IN ('verified','document_inconsistent','parse_failed',"
            "'unsupported','scanned','bad_password','error','needs_review')",
            name="parses_outcome_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    parser_version: Mapped[str]
    layout_slug: Mapped[str | None]
    layout_score: Mapped[float | None]
    runner_up_score: Mapped[float | None]
    outcome: Mapped[str]
    crossfoot_passed: Mapped[bool | None]
    failed_checks: Mapped[list[str]] = mapped_column(default=list)
    used_llm_fallback: Mapped[bool] = mapped_column(default=False)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


Index("parses_document_created_idx", Parse.document_id, Parse.created_at.desc())


class Correction(Base):
    __tablename__ = "corrections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"]
        ),
        CheckConstraint(
            "NOT is_gold OR crossfoot_passed", name="corrections_gold_passed_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    base_parse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parses.id"))
    corrected_json: Mapped[dict] = mapped_column(JSONB)
    crossfoot_passed: Mapped[bool]
    is_gold: Mapped[bool] = mapped_column(default=False)
    edit_count: Mapped[int]
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


Index(
    "one_gold_per_document", Correction.document_id, unique=True,
    postgresql_where=Correction.is_gold.is_(True),
)


class LayoutTemplate(Base):
    __tablename__ = "layout_templates"

    slug: Mapped[str] = mapped_column(primary_key=True)
    document_type: Mapped[str]
    country: Mapped[str]
    definition: Mapped[dict] = mapped_column(JSONB)
    source_parse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parses.id"))
    validated_successes: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=False)
    trusted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class RegressionRun(Base):
    __tablename__ = "regression_runs"
    __table_args__ = (
        CheckConstraint("split IN ('train','holdout','all')", name="regression_runs_split_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    parser_version: Mapped[str]
    split: Mapped[str]
    documents_run: Mapped[int]
    per_layout: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
```

```python
# crossfoot/db/__init__.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def get_engine(url: str):
    return create_engine(url)


def get_session(url: str) -> Session:
    return Session(get_engine(url))
```

- [ ] **Step 2: Initialize Alembic and point it at the models**

```bash
alembic init alembic
```

Edit `alembic/env.py`: import `crossfoot.db.models.Base` and `crossfoot.config.get_settings`, set `target_metadata = Base.metadata`, and set `config.set_main_option("sqlalchemy.url", get_settings().database_url)` inside `run_migrations_online`/`run_migrations_offline` before they read the URL.

- [ ] **Step 3: Generate and hand-check the initial migration**

```bash
alembic revision --autogenerate -m "initial schema"
```

Open the generated file under `alembic/versions/`, rename it to `0001_initial.py`, and confirm it includes `CREATE EXTENSION IF NOT EXISTS pgcrypto` (needed for `gen_random_uuid()`) — if autogenerate didn't add it, add manually as the first line of `upgrade()`:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
```

Also add, at the end of `upgrade()`, the seed for the one default tenant required by Global Constraints:

```python
op.execute(
    "INSERT INTO tenants (name) VALUES ('default') ON CONFLICT DO NOTHING"
)
```

- [ ] **Step 4: Run the migration**

```bash
alembic upgrade head
psql crossfoot -c "select name from tenants;"
```

Expected: one row, `default`.

- [ ] **Step 5: Write a model round-trip test**

```python
# tests/db/test_models.py
from crossfoot.config import get_settings
from crossfoot.db import get_session
from crossfoot.db.models import Tenant


def test_default_tenant_exists():
    with get_session(get_settings().database_url) as session:
        tenant = session.query(Tenant).filter_by(name="default").one()
        assert tenant.id is not None
```

```python
# tests/db/__init__.py
```

- [ ] **Step 6: Run the test**

Run: `pytest tests/db/test_models.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add crossfoot/db/ alembic.ini alembic/ tests/db/
git commit -m "feat: database schema, alembic migration, default tenant"
```

---

## Milestone 2: Validators and Outcome Logic

### Task 5: Bank statement validator (spec §6.1)

**Files:**
- Create: `crossfoot/doctypes/__init__.py`
- Create: `crossfoot/doctypes/bank_statement/__init__.py`
- Create: `crossfoot/doctypes/bank_statement/validator.py`
- Test: `tests/doctypes/test_bank_statement_validator.py`
- Test: `tests/doctypes/__init__.py`

**Interfaces:**
- Consumes: `Txn`, `ParsedStatement`, `CrossfootResult`, `RowBreak` from `crossfoot.types` (Task 2).
- Produces: `validate_bank_statement(p: ParsedStatement) -> CrossfootResult`, used by Milestone 4 layouts and the outcome logic in Task 7.

- [ ] **Step 1: Write the validator exactly per Technical PRD §6.1**

```python
# crossfoot/doctypes/bank_statement/validator.py
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
```

```python
# crossfoot/doctypes/__init__.py
```

```python
# crossfoot/doctypes/bank_statement/__init__.py
```

- [ ] **Step 2: Write the tests the Technical PRD explicitly calls for (§19): missing balance, newest-first ordering, derived opening balance, one-rupee break, page-continuity break**

```python
# tests/doctypes/test_bank_statement_validator.py
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/doctypes/test_bank_statement_validator.py -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Commit**

```bash
git add crossfoot/doctypes/ tests/doctypes/
git commit -m "feat: bank statement validator with zero-tolerance running balance check"
```

---

### Task 6: Credit card bill validator (spec §6.2)

Built even though no card PDFs were supplied — it is a small, fully-specified pure function, useful the moment a card bill is provided, and the Technical PRD build order (§17 step 6) treats it as part of Phase 0.

**Files:**
- Create: `crossfoot/doctypes/credit_card_bill/__init__.py`
- Create: `crossfoot/doctypes/credit_card_bill/validator.py`
- Test: `tests/doctypes/test_card_bill_validator.py`

**Interfaces:**
- Consumes: `CardTxn`, `ParsedCardBill`, `CrossfootResult` from `crossfoot.types`.
- Produces: `validate_card_bill(p: ParsedCardBill) -> CrossfootResult`.

- [ ] **Step 1: Implement exactly per §6.2**

```python
# crossfoot/doctypes/credit_card_bill/validator.py
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
```

```python
# crossfoot/doctypes/credit_card_bill/__init__.py
```

- [ ] **Step 2: Write tests covering refund, EMI, and tax rows (§6.2's "handles by construction" list)**

```python
# tests/doctypes/test_card_bill_validator.py
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/doctypes/test_card_bill_validator.py -v`
Expected: PASS (5 tests)

- [ ] **Step 4: Commit**

```bash
git add crossfoot/doctypes/credit_card_bill/ tests/doctypes/test_card_bill_validator.py
git commit -m "feat: credit card bill validator"
```

---

### Task 7: Outcome logic (spec §7)

**Files:**
- Create: `crossfoot/outcome.py`
- Test: `tests/test_outcome.py`

**Interfaces:**
- Consumes: `CrossfootResult`, `LayoutMatch`, `Outcome` from `crossfoot.types`.
- Produces: `decide_outcome(result: CrossfootResult, layout: LayoutMatch, structurally_clean: bool) -> Outcome`. Milestone 4/5 call this after every validation.

- [ ] **Step 1: Implement exactly per §7**

```python
# crossfoot/outcome.py
from crossfoot.types import CrossfootResult, LayoutMatch, Outcome

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

- [ ] **Step 2: Write tests covering the guardrail (untrusted layout can never produce `document_inconsistent`)**

```python
# tests/test_outcome.py
from crossfoot.outcome import decide_outcome
from crossfoot.types import CrossfootResult, LayoutMatch, RowBreak


def _result(passed, breaks=None, shape_errors=None, failed_checks=None):
    return CrossfootResult(
        passed=passed, checks=[], failed_checks=failed_checks or [],
        row_breaks=breaks or [], shape_errors=shape_errors or [],
    )


def test_passed_result_is_verified():
    layout = LayoutMatch(slug="x", score=1.0, trusted=False)
    assert decide_outcome(_result(True), layout, structurally_clean=True) == "verified"


def test_untrusted_layout_never_produces_document_inconsistent():
    layout = LayoutMatch(slug="x", score=1.0, trusted=False)
    result = _result(False, breaks=[RowBreak(0, 100, 99)], failed_checks=["running_balance"])
    assert decide_outcome(result, layout, structurally_clean=True) == "parse_failed"


def test_trusted_clean_localized_break_is_document_inconsistent():
    layout = LayoutMatch(slug="x", score=1.0, trusted=True)
    result = _result(False, breaks=[RowBreak(0, 100, 99)], failed_checks=["running_balance"])
    assert decide_outcome(result, layout, structurally_clean=True) == "document_inconsistent"


def test_too_many_breaks_is_parse_failed_even_when_trusted():
    layout = LayoutMatch(slug="x", score=1.0, trusted=True)
    breaks = [RowBreak(i, 100, 99) for i in range(4)]
    result = _result(False, breaks=breaks, failed_checks=["running_balance"])
    assert decide_outcome(result, layout, structurally_clean=True) == "parse_failed"


def test_not_structurally_clean_is_parse_failed_even_when_trusted():
    layout = LayoutMatch(slug="x", score=1.0, trusted=True)
    result = _result(False, breaks=[RowBreak(0, 100, 99)], failed_checks=["running_balance"])
    assert decide_outcome(result, layout, structurally_clean=False) == "parse_failed"


def test_dates_ordered_failure_is_never_document_inconsistent():
    layout = LayoutMatch(slug="x", score=1.0, trusted=True)
    result = _result(False, failed_checks=["dates_ordered"])
    assert decide_outcome(result, layout, structurally_clean=True) == "parse_failed"
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_outcome.py -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Commit**

```bash
git add crossfoot/outcome.py tests/test_outcome.py
git commit -m "feat: outcome decision logic with the untrusted-layout guardrail"
```

---

## Milestone 3: Synthetic PDFs and Extraction Pipeline

### Task 8: Synthetic bank statement generator (spec §17 step 1)

**Files:**
- Create: `crossfoot/synth.py`
- Test: `tests/test_synth.py`

**Interfaces:**
- Produces: `generate_statement_pdf(out_path: str, opening: Decimal, rows: list[tuple[date, str, Decimal|None, Decimal|None]], layout: str = "generic_v1") -> None` — writes a single-page-or-more PDF with a header (`Date | Narration | Debit | Credit | Balance`) and a running balance column computed from `opening` and each row's debit/credit, so the generated PDF is always internally consistent by construction. Used by Task 9-11's extraction tests so the pipeline can be built and proven without touching real bank PDFs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synth.py
from datetime import date
from decimal import Decimal

import fitz

from crossfoot.synth import generate_statement_pdf


def test_generated_pdf_contains_expected_words(tmp_path):
    out = tmp_path / "synth.pdf"
    rows = [
        (date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None),
        (date(2026, 9, 2), "UPI/CR/SALARY", None, Decimal("5000.00")),
    ]
    generate_statement_pdf(str(out), opening=Decimal("1000.00"), rows=rows)

    doc = fitz.open(str(out))
    text = doc[0].get_text()
    assert "UPI/DR/GROCERY" in text
    assert "900.00" in text     # 1000 - 100
    assert "5,900.00" in text   # 900 + 5000, grouped
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_synth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crossfoot.synth'`

- [ ] **Step 3: Implement**

```python
# crossfoot/synth.py
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

_COLUMNS = [("Date", 40), ("Narration", 120), ("Debit", 340), ("Credit", 420), ("Balance", 500)]


def _group(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    s = f"{abs(amount):,.2f}"
    # Indian grouping: last 3 digits, then groups of 2.
    int_part, dec_part = s.replace(",", "").split(".")
    if len(int_part) <= 3:
        grouped = int_part
    else:
        last3, rest = int_part[-3:], int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        grouped = ",".join(groups) + "," + last3
    return f"{sign}{grouped}.{dec_part}"


def generate_statement_pdf(out_path: str, opening: Decimal, rows: list[tuple], layout: str = "generic_v1"):
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    y = height - 60
    c.setFont("Helvetica-Bold", 10)
    for label, x in _COLUMNS:
        c.drawString(x, y, label)
    y -= 20
    c.setFont("Helvetica", 9)

    running = opening
    for txn_date, narration, debit, credit in rows:
        running = running + (credit or Decimal("0")) - (debit or Decimal("0"))
        c.drawString(_COLUMNS[0][1], y, txn_date.strftime("%d/%m/%Y"))
        c.drawString(_COLUMNS[1][1], y, narration)
        if debit is not None:
            c.drawString(_COLUMNS[2][1], y, _group(debit))
        if credit is not None:
            c.drawString(_COLUMNS[3][1], y, _group(credit))
        c.drawString(_COLUMNS[4][1], y, _group(running))
        y -= 18
        if y < 60:
            c.showPage()
            y = height - 60
    c.save()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_synth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crossfoot/synth.py tests/test_synth.py
git commit -m "feat: synthetic bank statement PDF generator for pipeline tests"
```

---

### Task 9: Extraction (spec §5.1–§5.2)

**Files:**
- Create: `crossfoot/pipeline/__init__.py`
- Create: `crossfoot/pipeline/extract.py`
- Test: `tests/pipeline/test_extract.py`
- Test: `tests/pipeline/__init__.py`

**Interfaces:**
- Consumes: `Word`, `ExtractedDoc` from `crossfoot.types`; `generate_statement_pdf` from `crossfoot.synth` (tests only).
- Produces: `extract(file_path: str, password: str | None = None) -> ExtractedDoc`. Raises `BadPasswordError` (defined here) if the password is wrong or missing on an encrypted PDF. Milestone 4 layouts and Task 10's table reconstruction consume `ExtractedDoc.pages`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/pipeline/test_extract.py
from datetime import date
from decimal import Decimal

from crossfoot.pipeline.extract import extract
from crossfoot.synth import generate_statement_pdf


def test_extract_returns_words_with_coordinates(tmp_path):
    out = tmp_path / "synth.pdf"
    generate_statement_pdf(
        str(out), opening=Decimal("1000.00"),
        rows=[(date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None)],
    )
    doc = extract(str(out))
    assert doc.page_count == 1
    assert doc.is_scanned is False
    words_text = {w.text for w in doc.pages[0]}
    assert "GROCERY" in " ".join(words_text) or any("GROCERY" in w for w in words_text)
    first_word = doc.pages[0][0]
    assert first_word.x1 > first_word.x0
    assert first_word.page == 0


def test_scanned_pdf_is_flagged(tmp_path):
    # A page with no extractable text at all simulates an image-only scan.
    import fitz
    out = tmp_path / "blank.pdf"
    d = fitz.open()
    d.new_page()
    d.save(str(out))
    doc = extract(str(out))
    assert doc.is_scanned is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pipeline/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/pipeline/extract.py
import fitz

from crossfoot.types import ExtractedDoc, Word

MIN_WORDS_PER_PAGE = 5


class BadPasswordError(Exception):
    pass


def extract(file_path: str, password: str | None = None) -> ExtractedDoc:
    doc = fitz.open(file_path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise BadPasswordError("incorrect or missing password")

    pages: list[list[Word]] = []
    first_page_text = ""
    for page_index in range(doc.page_count):
        page = doc[page_index]
        raw_words = page.get_text("words")
        words = [
            Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], page=page_index)
            for w in raw_words
        ]
        pages.append(words)
        if page_index == 0:
            first_page_text = page.get_text()

    is_scanned = doc.page_count == 0 or all(len(p) < MIN_WORDS_PER_PAGE for p in pages)

    return ExtractedDoc(
        pages=pages, page_count=doc.page_count,
        is_scanned=is_scanned, first_page_text=first_page_text,
    )
```

```python
# crossfoot/pipeline/__init__.py
```

```python
# tests/pipeline/__init__.py
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/pipeline/test_extract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crossfoot/pipeline/extract.py crossfoot/pipeline/__init__.py tests/pipeline/
git commit -m "feat: PDF word extraction with scanned-page detection"
```

---

### Task 10: Generic table reconstruction utility (spec §5.4)

This is the shared machinery every bank layout in Milestone 4 uses. It must support **both** the simple case (one physical text line per transaction row, e.g. HDFC) and the row-spans-multiple-lines case (SBI and IDFC each wrap narration across up to three physical lines per transaction, confirmed this session by inspecting real word coordinates).

**Files:**
- Create: `crossfoot/pipeline/tables.py`
- Test: `tests/pipeline/test_tables.py`

**Interfaces:**
- Consumes: `Word` from `crossfoot.types`.
- Produces:
  - `assign_column(word: Word, columns: list[tuple[str, float]]) -> str` — given column definitions `[(name, x_start), ...]` sorted by `x_start`, returns the name of the column whose range the word's `x0` falls into.
  - `band_rows(words: list[Word], anchor: callable[[list[Word]], bool], y_tolerance: float) -> list[list[Word]]` — groups words into row-bands. First splits `words` into physical lines (by rounding `y0` to the nearest 0.5pt), finds every line for which `anchor(line_words)` is `True` (the line that carries the row's date/amount — the "spine" of the row), then absorbs every non-anchor line within `y_tolerance` of an anchor line into that row-band, before the next anchor line. A non-anchor line farther than `y_tolerance` from any anchor is dropped (e.g. a page header/footer).

- [ ] **Step 1: Write the failing tests, using two synthetic scenarios: single-line rows and split-narration rows (hand-written coordinates, not real statement data)**

```python
# tests/pipeline/test_tables.py
from crossfoot.pipeline.tables import assign_column, band_rows
from crossfoot.types import Word


def _w(text, x0, y0, x1=None):
    return Word(text=text, x0=x0, y0=y0, x1=x1 or x0 + len(text) * 5, y1=y0 + 10, page=0)


def test_assign_column_picks_nearest_range():
    columns = [("date", 0.0), ("narration", 100.0), ("amount", 400.0)]
    assert assign_column(_w("01/09/2026", 10.0, 0), columns) == "date"
    assert assign_column(_w("UPI/DR/X", 150.0, 0), columns) == "narration"
    assert assign_column(_w("100.00", 420.0, 0), columns) == "amount"


def test_band_rows_single_line_per_row():
    # Two independent rows, each on its own line, no continuation lines.
    words = [
        _w("01/09/2026", 10, 100), _w("narration-a", 150, 100), _w("100.00", 420, 100),
        _w("02/09/2026", 10, 120), _w("narration-b", 150, 120), _w("200.00", 420, 120),
    ]
    has_date = lambda line: any(w.text.count("/") == 2 for w in line)
    rows = band_rows(words, anchor=has_date, y_tolerance=5.0)
    assert len(rows) == 2
    assert {w.text for w in rows[0]} == {"01/09/2026", "narration-a", "100.00"}


def test_band_rows_absorbs_narration_lines_above_and_below_the_anchor_line():
    # Mirrors the real SBI/IDFC layouts: a type/prefix line above the anchor
    # line, and a narration-continuation line below it, both within tolerance.
    words = [
        _w("WDL", 138, 93),          # line above (type code)
        _w("01/09/2026", 10, 100), _w("01/09/2026", 60, 100),
        _w("100.00", 420, 100), _w("900.00", 480, 100),   # anchor line
        _w("/GROCERY", 138, 102),    # line below (narration continuation)
        _w("02/09/2026", 10, 120), _w("02/09/2026", 60, 120),
        _w("200.00", 420, 120), _w("1100.00", 480, 120),
    ]
    has_date = lambda line: sum(1 for w in line if w.text.count("/") == 2) >= 2
    rows = band_rows(words, anchor=has_date, y_tolerance=8.0)
    assert len(rows) == 2
    row0_text = {w.text for w in rows[0]}
    assert "WDL" in row0_text and "/GROCERY" in row0_text and "100.00" in row0_text
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pipeline/test_tables.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/pipeline/tables.py
from crossfoot.types import Word


def assign_column(word: Word, columns: list[tuple[str, float]]) -> str:
    """columns: [(name, x_start), ...] sorted ascending by x_start."""
    name = columns[0][0]
    for col_name, x_start in columns:
        if word.x0 >= x_start:
            name = col_name
        else:
            break
    return name


def _group_into_lines(words: list[Word]) -> list[list[Word]]:
    lines: dict[float, list[Word]] = {}
    for w in words:
        key = round(w.y0 * 2) / 2   # nearest 0.5pt
        lines.setdefault(key, []).append(w)
    return [lines[y] for y in sorted(lines)]


def band_rows(words: list[Word], anchor, y_tolerance: float) -> list[list[Word]]:
    """Groups words into row-bands anchored on the line(s) for which anchor(line) is True.

    A non-anchor line is absorbed into the nearest anchor band (above or
    below) if within y_tolerance; otherwise it is dropped as page furniture.
    """
    lines = _group_into_lines(words)
    anchor_indices = [i for i, line in enumerate(lines) if anchor(line)]
    if not anchor_indices:
        return []

    bands: list[list[Word]] = [list(lines[i]) for i in anchor_indices]
    anchor_ys = [lines[i][0].y0 for i in anchor_indices]

    for i, line in enumerate(lines):
        if i in anchor_indices:
            continue
        line_y = line[0].y0
        distances = [abs(line_y - ay) for ay in anchor_ys]
        best = min(range(len(distances)), key=lambda k: distances[k])
        if distances[best] <= y_tolerance:
            bands[best].extend(line)

    return bands
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/pipeline/test_tables.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crossfoot/pipeline/tables.py tests/pipeline/test_tables.py
git commit -m "feat: generic column assignment and row-banding for multi-line table rows"
```

---

### Task 11: Layout registry and classification (spec §5.3)

**Files:**
- Create: `crossfoot/doctypes/bank_statement/layouts/__init__.py`
- Create: `crossfoot/doctypes/bank_statement/registry.py`
- Test: `tests/doctypes/test_registry.py`

**Interfaces:**
- Consumes: `ExtractedDoc`, `LayoutMatch` from `crossfoot.types`.
- Produces: `Layout` protocol (`slug`, `document_type`, `country`, `matches(doc) -> float`, `parse(doc) -> ParsedStatement`), `register(layout)`, `classify(doc: ExtractedDoc) -> tuple[LayoutMatch, LayoutMatch | None]` returning `(best, runner_up)` — `best.score <= 0.5` means no supported layout. Milestone 4 registers each bank's layout module here; Milestone 5's parse route and CLI call `classify`.

- [ ] **Step 1: Write the failing tests using two dummy layouts (no real bank data needed to test the registry itself)**

```python
# tests/doctypes/test_registry.py
from crossfoot.doctypes.bank_statement.registry import classify, register, _REGISTRY
from crossfoot.types import ExtractedDoc


class _FakeLayout:
    def __init__(self, slug, score):
        self.slug = slug
        self.document_type = "bank_statement"
        self.country = "IN"
        self._score = score

    def matches(self, doc):
        return self._score

    def parse(self, doc):
        raise NotImplementedError


def _empty_doc():
    return ExtractedDoc(pages=[[]], page_count=1, is_scanned=False, first_page_text="")


def test_classify_picks_highest_scoring_layout(monkeypatch):
    monkeypatch.setattr("crossfoot.doctypes.bank_statement.registry._REGISTRY",
                         [_FakeLayout("a", 0.6), _FakeLayout("b", 0.9)])
    best, runner_up = classify(_empty_doc())
    assert best.slug == "b"
    assert best.score == 0.9
    assert runner_up.slug == "a"


def test_classify_below_threshold_is_unsupported(monkeypatch):
    monkeypatch.setattr("crossfoot.doctypes.bank_statement.registry._REGISTRY",
                         [_FakeLayout("a", 0.2)])
    best, runner_up = classify(_empty_doc())
    assert best.score <= 0.5
    assert runner_up is None


def test_register_appends_to_registry():
    before = len(_REGISTRY)
    register(_FakeLayout("z", 0.1))
    assert len(_REGISTRY) == before + 1
    _REGISTRY.pop()   # keep the module-level registry clean for other tests
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/doctypes/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/doctypes/bank_statement/registry.py
from typing import Protocol

from crossfoot.types import ExtractedDoc, LayoutMatch, ParsedStatement

THRESHOLD = 0.5

_REGISTRY: list["Layout"] = []


class Layout(Protocol):
    slug: str
    document_type: str
    country: str

    def matches(self, doc: ExtractedDoc) -> float: ...
    def parse(self, doc: ExtractedDoc) -> ParsedStatement: ...


def register(layout: "Layout") -> None:
    _REGISTRY.append(layout)


def classify(doc: ExtractedDoc) -> tuple[LayoutMatch, LayoutMatch | None]:
    scored = sorted(
        ((layout, layout.matches(doc)) for layout in _REGISTRY),
        key=lambda pair: pair[1], reverse=True,
    )
    if not scored:
        return LayoutMatch(slug="none", score=0.0, trusted=False), None

    best_layout, best_score = scored[0]
    best = LayoutMatch(slug=best_layout.slug, score=best_score, trusted=False)
    if len(scored) < 2:
        return best, None
    runner_layout, runner_score = scored[1]
    runner_up = LayoutMatch(slug=runner_layout.slug, score=runner_score, trusted=False)
    return best, runner_up
```

```python
# crossfoot/doctypes/bank_statement/layouts/__init__.py
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/doctypes/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crossfoot/doctypes/bank_statement/registry.py crossfoot/doctypes/bank_statement/layouts/
git commit -m "feat: bank statement layout registry and classification"
```

---

## Milestone 4: Real Bank Layouts (SBI, HDFC, IDFC)

All three PDFs in `data/` were opened this session and confirmed to be **savings-account bank statements** (not card bills) with these real, bank-specific structures — recorded here so the layout code below is grounded in fact, not guesswork:

- **HDFC** (`data/HDFC_1.pdf`): one physical text line per transaction. A real header row exists with extractable text: `Date | Narration | Chq./Ref.No. | Value Dt | Withdrawal Amt. | Deposit Amt. | Closing Balance`. Dates are `DD/MM/YY` (two-digit year). Narration can spread onto extra lines with no date/amount — the simple "no date and no amount = continuation" rule (which the spec warns against as a *general* rule) is safe to use here specifically because HDFC's own continuation lines never contain a vertically-centered date.
- **SBI** (`data/SBI_1/2/3.pdf`): three physical text lines per transaction — a transaction-type line above the numeric line (e.g. `WDL TF`, `DEP TF`), the numeric/date line itself, and a narration-continuation line below. The table header is rendered without extractable text (only the word "Balance" survives as real text), so column boundaries must be hardcoded from observed geometry, not read from a header. Dates are `DD/MM/YYYY`. Empty debit/credit cells print a literal `-`.
- **IDFC** (`data/IDFC_1.pdf`): three physical text lines per transaction, similar to SBI. A real multi-line header exists: `Transaction Date | Value Date | Particulars | Cheque No | Debit | Credit | Balance`. Dates are `DD-MMM-YYYY`. The redaction tool's synthetic replacement reference numbers appear as free-floating 12-digit strings near each row and must not be read as amounts.

Because exact column boundaries were measured from only 1-2 pages per bank, each task below ends with a step that runs the layout against the **entire** real document and fixes any discrepancy the fuller page range reveals — this is the expected, spec-anticipated iteration (Technical PRD §1.2 calls debugging parsers against real documents "the real bottleneck"), not an open-ended task.

### Task 12: HDFC savings statement layout

**Files:**
- Create: `crossfoot/doctypes/bank_statement/layouts/hdfc_savings_v1.py`
- Test: `tests/doctypes/layouts/test_hdfc_savings_v1.py`
- Test: `tests/doctypes/layouts/__init__.py`

**Interfaces:**
- Consumes: `ExtractedDoc`, `Word`, `ParsedStatement`, `Txn` from `crossfoot.types`; `assign_column`, `band_rows` from `crossfoot.pipeline.tables`; `parse_amount`, `parse_date` from `crossfoot.countries.india`.
- Produces: `HdfcSavingsV1` instance implementing the `Layout` protocol (Task 11), registered under slug `"hdfc_savings_v1"`.

- [ ] **Step 1: Write a failing test using a small hand-written fixture that mimics HDFC's real column x-positions (illustrative narrations, not the real customer's transactions)**

```python
# tests/doctypes/layouts/test_hdfc_savings_v1.py
from decimal import Decimal

from crossfoot.doctypes.bank_statement.layouts.hdfc_savings_v1 import HdfcSavingsV1
from crossfoot.pipeline.extract import extract
from crossfoot.synth import generate_statement_pdf


def _make_words(rows):
    # rows: list of (date_str, narration, debit_str, deposit_str, balance_str)
    from crossfoot.types import Word
    words = []
    y = 240.0
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/doctypes/layouts/test_hdfc_savings_v1.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/doctypes/bank_statement/layouts/hdfc_savings_v1.py
from datetime import date as date_type

from crossfoot.countries.india import parse_amount, parse_date
from crossfoot.pipeline.tables import assign_column, band_rows
from crossfoot.types import ExtractedDoc, ParsedStatement, Txn

_COLUMNS = [
    ("date", 0.0), ("narration", 65.0), ("ref", 283.0),
    ("value_date", 350.0), ("debit", 405.0), ("credit", 491.0), ("balance", 564.0),
]
_HEADER_WORDS = {"Narration", "Withdrawal", "Deposit", "Closing"}


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
            table_words = [w for w in words if w.y0 > 231.5]
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
```

```python
# tests/doctypes/layouts/__init__.py
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/doctypes/layouts/test_hdfc_savings_v1.py -v`
Expected: PASS

- [ ] **Step 5: Register the layout**

```python
# crossfoot/doctypes/bank_statement/layouts/__init__.py
from crossfoot.doctypes.bank_statement.layouts.hdfc_savings_v1 import HdfcSavingsV1
from crossfoot.doctypes.bank_statement.registry import register

register(HdfcSavingsV1())
```

- [ ] **Step 6: Run against the real HDFC document and reconcile to `verified`**

```bash
source venv/bin/activate
python3 -c "
from crossfoot.pipeline.extract import extract
from crossfoot.doctypes.bank_statement.layouts.hdfc_savings_v1 import HdfcSavingsV1
from crossfoot.doctypes.bank_statement.validator import validate_bank_statement

doc = extract('data/HDFC_1.pdf')
layout = HdfcSavingsV1()
print('match score:', layout.matches(doc))
parsed = layout.parse(doc)
parsed.opening_balance = parsed.transactions[0].balance + (parsed.transactions[0].debit or 0) - (parsed.transactions[0].credit or 0) if parsed.transactions else None
print('rows:', len(parsed.transactions))
result = validate_bank_statement(parsed)
print('passed:', result.passed, 'failed:', result.failed_checks, 'breaks:', result.row_breaks[:5])
"
```

Inspect the output. If `result.passed` is `False`, use the specific `row_breaks` or `failed_checks` printed to find the mis-parsed row (common causes on a fuller page range: a narration line that collides with the `debit`/`credit` column ranges above, or a continuation line without a date that still needs absorbing) and adjust `_COLUMNS` or `_is_date_line` accordingly, re-running this step until it passes. Do not hand-adjust the real document's own numbers — only the parsing code.

- [ ] **Step 7: Commit**

```bash
git add crossfoot/doctypes/bank_statement/layouts/hdfc_savings_v1.py crossfoot/doctypes/bank_statement/layouts/__init__.py tests/doctypes/layouts/
git commit -m "feat: HDFC savings statement layout"
```

---

### Task 13: SBI savings statement layout

**Files:**
- Create: `crossfoot/doctypes/bank_statement/layouts/sbi_savings_v1.py`
- Test: `tests/doctypes/layouts/test_sbi_savings_v1.py`

**Interfaces:**
- Same as Task 12, registered under slug `"sbi_savings_v1"`.

- [ ] **Step 1: Write a failing test using a hand-written fixture that mimics SBI's real 3-line-per-row geometry (illustrative amounts, not the real customer's transactions)**

```python
# tests/doctypes/layouts/test_sbi_savings_v1.py
from decimal import Decimal

from crossfoot.doctypes.bank_statement.layouts.sbi_savings_v1 import SbiSavingsV1
from crossfoot.types import ExtractedDoc, Word


def _row(y, txn_date, txn_type, narration, ref_dash, debit, credit, balance):
    words = [
        Word(txn_type, 138.0, y - 7.2, 138.0 + len(txn_type) * 5, y - 0.2, 0),
        Word(txn_date, 27.5, y, 27.5 + 50, y + 9, 0),
        Word(txn_date, 82.5, y, 82.5 + 50, y + 9, 0),
        Word(ref_dash, 303.7, y, 305.7, y + 9, 0),
    ]
    if debit is not None:
        words.append(Word(debit, 351.9, y, 391.9, y + 9, 0))
        words.append(Word("-", 446.2, y, 448.2, y + 9, 0))
    else:
        words.append(Word("-", 366.2, y, 368.2, y + 9, 0))
        words.append(Word(credit, 429.7, y, 469.7, y + 9, 0))
    words.append(Word(balance, 512.2, y, 552.2, y + 9, 0))
    words.append(Word(narration, 138.0, y + 2.2, 138.0 + len(narration) * 5, y + 11.2, 0))
    return words


def test_matches_scores_high_when_balance_and_upi_narration_present():
    words = _row(536.2, "01/09/2026", "WDL TF", "UPI/DR//GROCERY", "-", "1,563.00", None, "23,334.37")
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    assert SbiSavingsV1().matches(doc) >= 0.6


def test_parse_debit_and_credit_rows_with_three_line_grouping():
    words = (
        _row(536.2, "01/09/2026", "WDL TF", "UPI/DR//GROCERY", "-", "1,563.00", None, "23,334.37")
        + _row(586.2, "01/09/2026", "DEP TF", "UPI/CR//SALARY", "-", None, "10,000.00", "33,334.37")
    )
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    parsed = SbiSavingsV1().parse(doc)
    assert len(parsed.transactions) == 2
    assert parsed.transactions[0].debit == Decimal("1563.00")
    assert parsed.transactions[0].credit is None
    assert "GROCERY" in parsed.transactions[0].narration
    assert parsed.transactions[1].credit == Decimal("10000.00")
    assert parsed.transactions[1].balance == Decimal("33334.37")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/doctypes/layouts/test_sbi_savings_v1.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/doctypes/bank_statement/layouts/sbi_savings_v1.py
from datetime import date as date_type

from crossfoot.countries.india import parse_amount, parse_date
from crossfoot.pipeline.tables import band_rows
from crossfoot.types import ExtractedDoc, ParsedStatement, Txn

# Column x-ranges measured from the real SBI e-statement (header row has no
# extractable text; only "Balance" survives, so these are hand-anchored).
_REF_X, _DEBIT_X, _CREDIT_X, _BALANCE_X = 290.0, 340.0, 400.0, 480.0


def _is_anchor_line(line) -> bool:
    date_count = 0
    for w in line:
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
        if "STATEMENT OF ACCOUNT" not in doc.first_page_text:
            return 0.0
        score = 0.3
        for words in doc.pages:
            if any(w.text == "Balance" for w in words):
                score += 0.3
            if any("UPI/" in w.text for w in words):
                score += 0.3
                break
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

                anchor_y = sorted(band, key=lambda w: w.y0)[len(band) // 2].y0
                narration_words = sorted(
                    (w for w in band if w.x0 < _REF_X and w not in dated_words),
                    key=lambda w: (w.y0, w.x0),
                )
                narration = " ".join(w.text for w in narration_words)

                debit = _amount_in_range(band, _DEBIT_X, _CREDIT_X)
                credit = _amount_in_range(band, _CREDIT_X, _BALANCE_X)
                balance = None
                for w in band:
                    if w.x0 >= _BALANCE_X:
                        balance = parse_amount(w.text)
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/doctypes/layouts/test_sbi_savings_v1.py -v`
Expected: PASS

- [ ] **Step 5: Register**

```python
# crossfoot/doctypes/bank_statement/layouts/__init__.py  (append)
from crossfoot.doctypes.bank_statement.layouts.sbi_savings_v1 import SbiSavingsV1
register(SbiSavingsV1())
```

- [ ] **Step 6: Run against all three real SBI documents and reconcile to `verified`**

```bash
source venv/bin/activate
python3 -c "
from crossfoot.pipeline.extract import extract
from crossfoot.doctypes.bank_statement.layouts.sbi_savings_v1 import SbiSavingsV1
from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
from decimal import Decimal

for path in ['data/SBI_1.pdf', 'data/SBI_2.pdf', 'data/SBI_3.pdf']:
    doc = extract(path)
    layout = SbiSavingsV1()
    parsed = layout.parse(doc)
    if parsed.transactions:
        first = parsed.transactions[0]
        parsed.opening_balance = first.balance + (first.debit or Decimal(0)) - (first.credit or Decimal(0))
    result = validate_bank_statement(parsed)
    print(path, 'match:', layout.matches(doc), 'rows:', len(parsed.transactions),
          'passed:', result.passed, 'failed:', result.failed_checks, 'breaks:', result.row_breaks[:3])
"
```

For any file that doesn't pass, print the offending band's raw words (`band_rows` output) to see which line the narration-vs-type-code split logic mis-grouped, then adjust `_REF_X`/`_DEBIT_X`/`_CREDIT_X`/`_BALANCE_X` or the narration line selection, and re-run. This step is done only when all three real SBI statements parse to `verified` (opening balance derived from the first row, since SBI's own printed opening balance lives in unstructured header text this layout doesn't yet read — acceptable for Phase 0-A; note it as a follow-up rather than blocking on parsing the header).

- [ ] **Step 7: Commit**

```bash
git add crossfoot/doctypes/bank_statement/layouts/sbi_savings_v1.py crossfoot/doctypes/bank_statement/layouts/__init__.py tests/doctypes/layouts/test_sbi_savings_v1.py
git commit -m "feat: SBI savings statement layout with three-line row grouping"
```

---

### Task 14: IDFC savings statement layout

**Files:**
- Create: `crossfoot/doctypes/bank_statement/layouts/idfc_savings_v1.py`
- Test: `tests/doctypes/layouts/test_idfc_savings_v1.py`

**Interfaces:**
- Same as Task 12/13, registered under slug `"idfc_savings_v1"`.

- [ ] **Step 1: Write a failing test using a hand-written fixture mimicking IDFC's real header and 3-line row geometry (illustrative narrations, not the real customer's transactions)**

```python
# tests/doctypes/layouts/test_idfc_savings_v1.py
from decimal import Decimal

from crossfoot.doctypes.bank_statement.layouts.idfc_savings_v1 import IdfcSavingsV1
from crossfoot.types import ExtractedDoc, Word

_HEADER = [
    ("Transaction", 47.0, 460.9), ("Cheque", 304.5, 460.9),
    ("Value", 128.1, 466.4), ("Date", 151.2, 466.4), ("Particulars", 219.6, 466.4),
    ("Debit", 374.9, 466.4), ("Credit", 452.2, 466.4), ("Balance", 527.2, 466.4),
    ("Date", 60.8, 471.9), ("No", 313.8, 471.9),
]


def _header_words():
    return [Word(t, x, y, x + len(t) * 5, y + 9, 0) for t, x, y in _HEADER]


def _row(y, txn_date, prefix, mid, suffix, debit, credit, balance):
    words = [
        Word(prefix, 192.7, y - 11.0, 192.7 + len(prefix) * 5, y - 2.0, 0),
        Word(txn_date, 35.0, y, 35.0 + 60, y + 9, 0),
        Word(txn_date, 113.9, y, 113.9 + 60, y + 9, 0),
        Word(mid, 223.7, y, 223.7 + len(mid) * 5, y + 9, 0),
    ]
    if debit is not None:
        words.append(Word(debit, 383.7, y, 423.7, y + 9, 0))
    if credit is not None:
        words.append(Word(credit, 467.0, y, 507.0, y + 9, 0))
    words.append(Word(balance, 541.4, y, 581.4, y + 9, 0))
    words.append(Word(suffix, 192.7, y + 11.0, 192.7 + len(suffix) * 5, y + 20.0, 0))
    return words


def test_matches_scores_high_on_real_header():
    doc = ExtractedDoc(pages=[_header_words()], page_count=1, is_scanned=False,
                        first_page_text="STATEMENT OF ACCOUNT")
    assert IdfcSavingsV1().matches(doc) >= 0.6


def test_parse_debit_and_credit_rows_with_three_line_narration():
    words = _header_words()
    words += _row(519.3, "01-Sep-2026", "UPI/DR/", "/KKBK/", "Example Merchant", "51,507.96", None, "50,997.04")
    words += _row(556.9, "03-Sep-2026", "UPI/CR/", "/SBIN/", "Example Payer", None, "2,000.00", "52,997.04")
    doc = ExtractedDoc(pages=[words], page_count=1, is_scanned=False, first_page_text="STATEMENT OF ACCOUNT")
    parsed = IdfcSavingsV1().parse(doc)
    assert len(parsed.transactions) == 2
    assert parsed.transactions[0].debit == Decimal("51507.96")
    assert "Example Merchant" in parsed.transactions[0].narration
    assert parsed.transactions[1].credit == Decimal("2000.00")
    assert parsed.transactions[1].balance == Decimal("52997.04")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/doctypes/layouts/test_idfc_savings_v1.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/doctypes/bank_statement/layouts/idfc_savings_v1.py
from datetime import date as date_type

from crossfoot.countries.india import parse_amount, parse_date
from crossfoot.pipeline.tables import band_rows
from crossfoot.types import ExtractedDoc, ParsedStatement, Txn

_PARTICULARS_MAX_X, _DEBIT_X, _CREDIT_X, _BALANCE_X = 374.0, 374.0, 452.0, 527.0
_HEADER_WORDS = {"Transaction", "Particulars", "Cheque", "Balance"}


def _is_anchor_line(line) -> bool:
    date_count = sum(1 for w in line if _looks_like_date(w.text))
    return date_count >= 2


def _looks_like_date(text: str) -> bool:
    try:
        parse_date(text)
        return True
    except ValueError:
        return False


def _is_reference_noise(text: str) -> bool:
    # The redaction tool's synthetic replacement reference numbers: bare
    # 12+ digit strings with no separators, distinct from amounts (which
    # always carry a decimal point in this layout).
    return text.isdigit() and len(text) >= 10


class IdfcSavingsV1:
    slug = "idfc_savings_v1"
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
                        debit = parse_amount(w.text)
                        break
                credit = None
                for w in band:
                    if _CREDIT_X <= w.x0 < _BALANCE_X:
                        credit = parse_amount(w.text)
                        break
                balance = None
                for w in band:
                    if w.x0 >= _BALANCE_X:
                        balance = parse_amount(w.text)
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/doctypes/layouts/test_idfc_savings_v1.py -v`
Expected: PASS

- [ ] **Step 5: Register**

```python
# crossfoot/doctypes/bank_statement/layouts/__init__.py  (append)
from crossfoot.doctypes.bank_statement.layouts.idfc_savings_v1 import IdfcSavingsV1
register(IdfcSavingsV1())
```

- [ ] **Step 6: Run against the real IDFC document and reconcile to `verified`**

Same pattern as Task 12/13 Step 6, against `data/IDFC_1.pdf`. IDFC's own statement prints an explicit opening balance in structured header text (`Opening Balance` / `Total Debit` / `Total Credit` / `Closing Balance` row, confirmed this session at real y≈388.7–404.4) — extend `parse()` to read that row (four amounts in that order, matched by x-range against the same header words) and set `opening_balance`/`closing_balance` on the returned `ParsedStatement` rather than leaving them `None`, before iterating on any remaining row breaks.

- [ ] **Step 7: Commit**

```bash
git add crossfoot/doctypes/bank_statement/layouts/idfc_savings_v1.py crossfoot/doctypes/bank_statement/layouts/__init__.py tests/doctypes/layouts/test_idfc_savings_v1.py
git commit -m "feat: IDFC savings statement layout with three-line row grouping"
```

---

### Task 15: End-to-end regression fixture over all five real documents

**Files:**
- Create: `tests/test_real_documents_e2e.py`

**Interfaces:**
- Consumes: `extract`, `classify`, `validate_bank_statement`, `decide_outcome`, `LayoutMatch` — the full pipeline built in Tasks 9-14.

- [ ] **Step 1: Write a test that runs the whole pipeline end-to-end on every file in `data/` and asserts each reaches `verified`**

```python
# tests/test_real_documents_e2e.py
import glob

import pytest

from crossfoot.doctypes.bank_statement.registry import classify
from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
from crossfoot.outcome import decide_outcome
from crossfoot.pipeline.extract import extract
from crossfoot.types import LayoutMatch


@pytest.mark.parametrize("path", sorted(glob.glob("data/*.pdf")))
def test_real_statement_reaches_verified(path):
    doc = extract(path)
    assert not doc.is_scanned, f"{path} flagged as scanned"

    best, _runner_up = classify(doc)
    assert best.score > 0.5, f"{path} unsupported, best score {best.score}"

    from crossfoot.doctypes.bank_statement.registry import _REGISTRY
    layout = next(l for l in _REGISTRY if l.slug == best.slug)
    parsed = layout.parse(doc)
    if parsed.opening_balance is None and parsed.transactions:
        first = parsed.transactions[0]
        parsed.opening_balance = first.balance + (first.debit or 0) - (first.credit or 0)

    result = validate_bank_statement(parsed)
    outcome = decide_outcome(result, LayoutMatch(best.slug, best.score, trusted=True), structurally_clean=True)
    assert outcome == "verified", (
        f"{path} via {best.slug}: failed_checks={result.failed_checks}, "
        f"row_breaks={result.row_breaks[:5]}"
    )
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_real_documents_e2e.py -v`
Expected: PASS for all 5 files (`data/SBI_1.pdf`, `data/SBI_2.pdf`, `data/SBI_3.pdf`, `data/HDFC_1.pdf`, `data/IDFC_1.pdf`). If any fails, return to that bank's Task (12/13/14) Step 6 and continue iterating — do not weaken this test's assertion to make it pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_real_documents_e2e.py
git commit -m "test: end-to-end verification across all five real training statements"
```

---

## Milestone 5: Playground Web App and CLI

### Task 16: FastAPI app skeleton and upload route

**Files:**
- Create: `crossfoot/app.py`
- Create: `crossfoot/templates/base.html`
- Create: `crossfoot/templates/upload.html`
- Delete: `main.py`, `test_main.http` (superseded)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `crossfoot.app.app` (a `FastAPI` instance), `crossfoot.app.create_app(settings) -> FastAPI` (used by `uvicorn` and by tests, so tests can inject a settings object without touching env vars).

- [ ] **Step 1: Write a failing test for the upload flow**

```python
# tests/test_app.py
from datetime import date
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient

from crossfoot.app import create_app
from crossfoot.config import Settings
from crossfoot.synth import generate_statement_pdf


def _client(tmp_path, db_url):
    settings = Settings(database_url=db_url, mode="playground")
    return TestClient(create_app(settings))


def test_get_upload_page(tmp_path):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url)
    response = client.get("/upload")
    assert response.status_code == 200
    assert b"Upload" in response.content


def test_post_upload_creates_a_document_and_a_parse(tmp_path):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url)
    pdf_path = tmp_path / "synth.pdf"
    generate_statement_pdf(str(pdf_path), opening=Decimal("1000.00"), rows=[
        (date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None),
    ])
    with open(pdf_path, "rb") as f:
        response = client.post(
            "/upload",
            data={"document_type": "bank_statement", "country": "IN", "split": "train",
                  "source": "self", "owner_label": "test"},
            files={"file": ("synth.pdf", f, "application/pdf")},
        )
    assert response.status_code in (200, 303)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crossfoot.app'`

- [ ] **Step 3: Implement the templates**

```html
<!-- crossfoot/templates/base.html -->
<!doctype html>
<html>
<head><title>Crossfoot Playground</title></head>
<body>
  <nav><a href="/upload">Upload</a> | <a href="/documents">Documents</a></nav>
  {% block content %}{% endblock %}
</body>
</html>
```

```html
<!-- crossfoot/templates/upload.html -->
{% extends "base.html" %}
{% block content %}
<h1>Upload</h1>
<form method="post" action="/upload" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <select name="document_type"><option value="bank_statement">Bank statement</option></select>
  <input type="text" name="country" value="IN">
  <select name="split"><option value="train">train</option><option value="holdout">holdout</option></select>
  <select name="source">
    <option value="self">self</option><option value="family_friend">family_friend</option>
    <option value="concierge">concierge</option>
  </select>
  <input type="text" name="owner_label" placeholder="owner label" required>
  <input type="text" name="consent_note" placeholder="consent note (required unless source=self)">
  <input type="password" name="password" placeholder="PDF password (optional)">
  <button type="submit">Parse</button>
</form>
{% endblock %}
```

- [ ] **Step 4: Implement the app**

```python
# crossfoot/app.py
import hashlib
import shutil
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from crossfoot.config import Settings, get_settings
from crossfoot.db import get_session
from crossfoot.db.models import Document, Parse
from crossfoot.doctypes.bank_statement.registry import _REGISTRY, classify
from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
from crossfoot.outcome import decide_outcome
from crossfoot.pipeline.extract import BadPasswordError, extract
from crossfoot.types import LayoutMatch

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
DATA_DIR = Path("data")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.mode != "playground":
        raise RuntimeError("crossfoot.app only serves playground mode in Phase 0-A")

    app = FastAPI()

    @app.get("/upload")
    def upload_form(request: Request):
        return TEMPLATES.TemplateResponse(request, "upload.html", {})

    @app.post("/upload")
    def upload_submit(
        file: UploadFile,
        document_type: str = Form(...),
        country: str = Form(...),
        split: str = Form(...),
        source: str = Form(...),
        owner_label: str = Form(...),
        consent_note: str | None = Form(None),
        password: str | None = Form(None),
    ):
        raw = file.file.read()
        sha256 = hashlib.sha256(raw).hexdigest()
        DATA_DIR.mkdir(exist_ok=True)
        storage_path = DATA_DIR / f"{sha256}.pdf"
        storage_path.write_bytes(raw)

        with get_session(settings.database_url) as session:
            tenant_id = session.execute(
                __import__("sqlalchemy").text("select id from tenants where name = 'default'")
            ).scalar_one()
            doc_row = Document(
                tenant_id=tenant_id, document_type=document_type, country=country,
                currency="INR", split=split, source=source, owner_label=owner_label,
                consent_note=consent_note, storage_path=str(storage_path), sha256=sha256,
            )
            session.add(doc_row)
            session.flush()

            try:
                extracted = extract(str(storage_path), password=password)
            except BadPasswordError:
                parse_row = Parse(tenant_id=tenant_id, document_id=doc_row.id,
                                   parser_version="dirty", outcome="bad_password", rows_checked=0)
                session.add(parse_row)
                session.commit()
                return RedirectResponse(f"/documents/{doc_row.id}", status_code=303)

            if extracted.is_scanned:
                outcome = "scanned"
                layout_slug, layout_score, output_json, failed_checks = None, None, None, []
            else:
                best, _runner_up = classify(extracted)
                if best.score <= 0.5:
                    outcome, layout_slug, layout_score, output_json, failed_checks = (
                        "unsupported", None, best.score, None, [])
                else:
                    layout = next(l for l in _REGISTRY if l.slug == best.slug)
                    parsed = layout.parse(extracted)
                    if parsed.opening_balance is None and parsed.transactions:
                        first = parsed.transactions[0]
                        parsed.opening_balance = first.balance + (first.debit or 0) - (first.credit or 0)
                    result = validate_bank_statement(parsed)
                    outcome = decide_outcome(
                        result, LayoutMatch(best.slug, best.score, trusted=True), structurally_clean=True
                    )
                    layout_slug, layout_score = best.slug, best.score
                    failed_checks = result.failed_checks
                    output_json = {
                        "transactions": len(parsed.transactions),
                        "closing_balance": str(parsed.closing_balance),
                    }

            parse_row = Parse(
                tenant_id=tenant_id, document_id=doc_row.id, parser_version="dirty",
                layout_slug=layout_slug, layout_score=layout_score, outcome=outcome,
                crossfoot_passed=(outcome == "verified"), failed_checks=failed_checks,
                output_json=output_json,
            )
            session.add(parse_row)
            session.commit()

        return RedirectResponse(f"/documents/{doc_row.id}", status_code=303)

    @app.get("/documents/{document_id}")
    def document_detail(document_id: str):
        return {"document_id": document_id}

    return app


app = create_app()
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_app.py -v`
Expected: PASS

- [ ] **Step 6: Remove the superseded stub files**

```bash
git rm main.py test_main.http
```

- [ ] **Step 7: Commit**

```bash
git add crossfoot/app.py crossfoot/templates/
git commit -m "feat: playground FastAPI app with upload and pipeline wiring"
```

---

### Task 17: Documents list and detail pages

**Files:**
- Create: `crossfoot/templates/documents.html`
- Create: `crossfoot/templates/document_detail.html`
- Modify: `crossfoot/app.py`
- Test: `tests/test_app.py` (extend)

**Interfaces:**
- Consumes: `Document`, `Parse` models (Task 4); `create_app` from Task 16.
- Produces: `GET /documents` (filterable list, held-out hidden by default per §14), `GET /documents/{id}` (full detail: latest parse outcome, failed checks, transactions from `output_json`).

- [ ] **Step 1: Extend the failing test**

```python
# tests/test_app.py (add)
def test_documents_list_hides_holdout_by_default(tmp_path):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url)
    response = client.get("/documents")
    assert response.status_code == 200


def test_documents_list_shows_holdout_when_requested(tmp_path):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url)
    response = client.get("/documents?show_holdout=true")
    assert response.status_code == 200
```

- [ ] **Step 2: Run to verify failure/pass status** (these may already pass trivially with a 404 turned 200; check the assertion is meaningful by first confirming route absence returns 404)

Run: `pytest tests/test_app.py::test_documents_list_hides_holdout_by_default -v`
Expected: FAIL with 404 before Step 4.

- [ ] **Step 3: Write the templates**

```html
<!-- crossfoot/templates/documents.html -->
{% extends "base.html" %}
{% block content %}
<h1>Documents</h1>
<table>
  <tr><th>ID</th><th>Type</th><th>Country</th><th>Split</th><th>Outcome</th></tr>
  {% for doc, outcome in rows %}
  <tr>
    <td><a href="/documents/{{ doc.id }}">{{ doc.id }}</a></td>
    <td>{{ doc.document_type }}</td><td>{{ doc.country }}</td>
    <td>{{ doc.split }}</td><td>{{ outcome or "pending" }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

```html
<!-- crossfoot/templates/document_detail.html -->
{% extends "base.html" %}
{% block content %}
<h1>Document {{ doc.id }}</h1>
<p>Outcome: {{ parse.outcome if parse else "no parse" }}</p>
<p>Failed checks: {{ parse.failed_checks if parse else [] }}</p>
<pre>{{ parse.output_json if parse else {} }}</pre>
{% endblock %}
```

- [ ] **Step 4: Add the routes to `crossfoot/app.py`** (both routes take `request: Request` as their first parameter, matching the `upload_form` route's pattern from Task 16, because `TemplateResponse` requires it)

```python
    @app.get("/documents")
    def documents_list(request: Request, show_holdout: bool = False):
        from sqlalchemy import select
        with get_session(settings.database_url) as session:
            query = select(Document)
            if not show_holdout:
                query = query.where(Document.split == "train")
            docs = session.scalars(query.order_by(Document.uploaded_at.desc())).all()
            rows = []
            for doc in docs:
                latest = session.scalars(
                    select(Parse).where(Parse.document_id == doc.id)
                    .order_by(Parse.created_at.desc()).limit(1)
                ).first()
                rows.append((doc, latest.outcome if latest else None))
        return TEMPLATES.TemplateResponse(request, "documents.html", {"rows": rows})

    @app.get("/documents/{document_id}")
    def document_detail(request: Request, document_id: str):
        from sqlalchemy import select
        with get_session(settings.database_url) as session:
            doc = session.get(Document, document_id)
            parse = session.scalars(
                select(Parse).where(Parse.document_id == document_id)
                .order_by(Parse.created_at.desc()).limit(1)
            ).first()
        return TEMPLATES.TemplateResponse(request, "document_detail.html", {"doc": doc, "parse": parse})
```

Replace the earlier placeholder `document_detail` route from Task 16 with this one (same path, real implementation).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_app.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add crossfoot/app.py crossfoot/templates/documents.html crossfoot/templates/document_detail.html tests/test_app.py
git commit -m "feat: documents list and detail pages, held-out hidden by default"
```

---

### Task 18: Correction UI (re-validate, save, mark gold)

**Files:**
- Modify: `crossfoot/app.py`
- Modify: `crossfoot/templates/document_detail.html`
- Test: `tests/test_corrections.py`

**Interfaces:**
- Consumes: `Correction` model (Task 4), `validate_bank_statement` (Task 5).
- Produces: `POST /documents/{id}/revalidate` (body: corrected transaction JSON; returns `CrossfootResult` without saving), `POST /documents/{id}/corrections` (saves a `Correction` row with `edit_count`), `POST /documents/{id}/corrections/{correction_id}/gold` (sets `is_gold=True`, unsets any prior gold for the document in the same transaction, per §10's comment).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_corrections.py
import os
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from crossfoot.app import create_app
from crossfoot.config import Settings
from crossfoot.db import get_session
from crossfoot.db.models import Document
from sqlalchemy import text as sa_text


def _client():
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    return TestClient(create_app(Settings(database_url=db_url, mode="playground"))), db_url


def _make_document(db_url):
    with get_session(db_url) as session:
        tenant_id = session.execute(sa_text("select id from tenants where name='default'")).scalar_one()
        doc = Document(tenant_id=tenant_id, document_type="bank_statement", country="IN",
                        currency="INR", split="train", source="self", owner_label="test",
                        sha256="deadbeef" + str(id(session)))
        session.add(doc)
        session.commit()
        return str(doc.id)


def test_revalidate_does_not_persist_a_correction():
    client, db_url = _client()
    doc_id = _make_document(db_url)
    payload = {
        "opening_balance": "1000.00",
        "transactions": [
            {"date": "2026-09-01", "narration": "x", "reference": None,
             "debit": "100.00", "credit": None, "balance": "900.00", "page": 0}
        ],
    }
    response = client.post(f"/documents/{doc_id}/revalidate", json=payload)
    assert response.status_code == 200
    assert response.json()["passed"] is True

    with get_session(db_url) as session:
        from crossfoot.db.models import Correction
        assert session.query(Correction).filter_by(document_id=doc_id).count() == 0


def test_save_correction_then_mark_gold():
    client, db_url = _client()
    doc_id = _make_document(db_url)
    payload = {
        "opening_balance": "1000.00",
        "transactions": [
            {"date": "2026-09-01", "narration": "x", "reference": None,
             "debit": "100.00", "credit": None, "balance": "900.00", "page": 0}
        ],
        "edit_count": 1,
    }
    response = client.post(f"/documents/{doc_id}/corrections", json=payload)
    assert response.status_code == 200
    correction_id = response.json()["id"]

    gold_response = client.post(f"/documents/{doc_id}/corrections/{correction_id}/gold")
    assert gold_response.status_code == 200

    with get_session(db_url) as session:
        from crossfoot.db.models import Correction
        golds = session.query(Correction).filter_by(document_id=doc_id, is_gold=True).all()
        assert len(golds) == 1
        assert str(golds[0].id) == correction_id
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_corrections.py -v`
Expected: FAIL (404s on the new routes)

- [ ] **Step 3: Implement, adding to `crossfoot/app.py`**

```python
    @app.post("/documents/{document_id}/revalidate")
    def revalidate(document_id: str, payload: dict):
        parsed = _statement_from_payload(payload)
        result = validate_bank_statement(parsed)
        return {
            "passed": result.passed,
            "failed_checks": result.failed_checks,
            "row_breaks": [{"row_index": b.row_index, "expected": str(b.expected), "printed": str(b.printed)}
                            for b in result.row_breaks],
        }

    @app.post("/documents/{document_id}/corrections")
    def save_correction(document_id: str, payload: dict):
        parsed = _statement_from_payload(payload)
        result = validate_bank_statement(parsed)
        with get_session(settings.database_url) as session:
            doc = session.get(Document, document_id)
            correction = Correction(
                tenant_id=doc.tenant_id, document_id=document_id, corrected_json=payload,
                crossfoot_passed=result.passed, edit_count=payload.get("edit_count", 0),
            )
            session.add(correction)
            session.commit()
            return {"id": str(correction.id), "passed": result.passed}

    @app.post("/documents/{document_id}/corrections/{correction_id}/gold")
    def mark_gold(document_id: str, correction_id: str):
        from crossfoot.db.models import Correction
        with get_session(settings.database_url) as session:
            target = session.get(Correction, correction_id)
            if not target.crossfoot_passed:
                return {"error": "cannot mark a failing correction as gold"}
            session.query(Correction).filter_by(document_id=document_id, is_gold=True).update(
                {"is_gold": False}
            )
            target.is_gold = True
            session.commit()
            return {"id": str(target.id), "is_gold": True}
```

Add the shared helper near the top of `crossfoot/app.py`:

```python
def _statement_from_payload(payload: dict):
    from datetime import date as date_cls
    from decimal import Decimal

    from crossfoot.types import ParsedStatement, Txn

    def _dec(v):
        return Decimal(v) if v is not None else None

    txns = [
        Txn(date=date_cls.fromisoformat(t["date"]), narration=t["narration"],
            reference=t.get("reference"), debit=_dec(t.get("debit")), credit=_dec(t.get("credit")),
            balance=_dec(t.get("balance")), page=t.get("page", 0))
        for t in payload["transactions"]
    ]
    return ParsedStatement(
        currency="INR", locale="en-IN",
        period_from=txns[0].date if txns else date_cls.today(),
        period_to=txns[-1].date if txns else date_cls.today(),
        opening_balance=_dec(payload.get("opening_balance")),
        closing_balance=_dec(payload.get("closing_balance")),
        brought_forward={}, transactions=txns, masked_account=None,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_corrections.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crossfoot/app.py tests/test_corrections.py
git commit -m "feat: correction UI backend (revalidate, save, mark gold)"
```

---

### Task 19: CLI — parse, redact, fixture, synth

**Files:**
- Create: `crossfoot/cli.py`
- Modify: `crossfoot/redact.py` (moved from root `redact.py`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: console entry point `crossfoot` (registered in `pyproject.toml` Task 1) with subcommands `parse`, `redact`, `fixture`, `synth`, dispatched via `argparse`.
- Consumes: `crossfoot.db.models.Document`/`Correction` (Task 4) — `redact` looks up a document's `storage_path` and `split` before delegating to the redaction script (refusing `split == "holdout"` per Technical PRD §9.2); `fixture` looks up the document's gold `Correction` (`is_gold=True`) and writes its `corrected_json` to the given output path. Both use `crossfoot.config.get_settings()` / `crossfoot.db.get_session()`, the same pattern as `crossfoot/app.py` (Task 16).

- [ ] **Step 1: Move the existing redaction script into the package**

```bash
git mv redact.py crossfoot/redact.py
git mv terms.txt crossfoot/terms.txt
```

Update any relative path the script assumes for `terms.txt` (check the top-level `--terms-file` default, if any, and point it at the moved location or keep it as an explicit CLI arg — do not hardcode a path that breaks when installed as a package).

**Also fix `main()`'s signature while moving the file:** the script currently defines `def main():` and calls `args = ap.parse_args()` (reading `sys.argv` directly), which cannot be invoked programmatically from `crossfoot/cli.py`'s `_cmd_redact` (Step 4 below needs to call it with an explicit argument list, e.g. from a document's real `storage_path`, not from `sys.argv`). Change the signature to `def main(argv=None):` and the parse call to `args = ap.parse_args(argv)` — this is the standard idiom (`argv=None` makes `argparse` default to `sys.argv[1:]`, so `python redact.py in.pdf out.pdf` on the command line is unaffected; passing an explicit list is what makes programmatic invocation possible). Leave everything else in the file unchanged.

- [ ] **Step 2: Write failing CLI tests**

```python
# tests/test_cli.py
import subprocess
import sys
from datetime import date
from decimal import Decimal

from crossfoot.synth import generate_statement_pdf


def test_parse_command_prints_outcome(tmp_path):
    pdf_path = tmp_path / "synth.pdf"
    generate_statement_pdf(str(pdf_path), opening=Decimal("1000.00"), rows=[
        (date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None),
    ])
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "parse", str(pdf_path),
         "--type", "bank_statement", "--country", "IN"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "outcome" in result.stdout.lower()


def test_synth_command_writes_a_pdf(tmp_path):
    out_dir = tmp_path / "pdfs"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "synth", "--layout", "generic_v1",
         "--months", "1", "--out", str(out_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert list(out_dir.glob("*.pdf"))


def test_fixture_command_writes_gold_correction_json(tmp_path):
    import json
    import os
    from sqlalchemy import text as sa_text

    from crossfoot.config import get_settings
    from crossfoot.db import get_session
    from crossfoot.db.models import Correction, Document

    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", get_settings().database_url)
    with get_session(db_url) as session:
        tenant_id = session.execute(sa_text("select id from tenants where name='default'")).scalar_one()
        doc = Document(
            tenant_id=tenant_id, document_type="bank_statement", country="IN", currency="INR",
            split="train", source="self", owner_label="fixture-test",
            sha256=f"fixturetest{os.getpid()}{id(session)}",
        )
        session.add(doc)
        session.flush()
        gold = Correction(
            tenant_id=tenant_id, document_id=doc.id,
            corrected_json={"transactions": [], "closing_balance": "0.00"},
            crossfoot_passed=True, is_gold=True, edit_count=1,
        )
        session.add(gold)
        session.commit()
        document_id = str(doc.id)

    out_path = tmp_path / "fixture.json"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "fixture", document_id, "--out", str(out_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    written = json.loads(out_path.read_text())
    assert written == {"transactions": [], "closing_balance": "0.00"}


def test_fixture_command_fails_cleanly_with_no_gold_correction(tmp_path):
    out_path = tmp_path / "fixture.json"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "fixture", "00000000-0000-0000-0000-000000000000",
         "--out", str(out_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert not out_path.exists()


def test_redact_command_refuses_holdout_document(tmp_path):
    import os
    from sqlalchemy import text as sa_text

    from crossfoot.config import get_settings
    from crossfoot.db import get_session
    from crossfoot.db.models import Document

    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", get_settings().database_url)
    with get_session(db_url) as session:
        tenant_id = session.execute(sa_text("select id from tenants where name='default'")).scalar_one()
        doc = Document(
            tenant_id=tenant_id, document_type="bank_statement", country="IN", currency="INR",
            split="holdout", source="self", owner_label="redact-test",
            sha256=f"redacttest{os.getpid()}{id(session)}", storage_path=str(tmp_path / "irrelevant.pdf"),
        )
        session.add(doc)
        session.commit()
        document_id = str(doc.id)

    out_path = tmp_path / "redacted.pdf"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "redact", document_id, "--out", str(out_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "holdout" in result.stdout.lower()
    assert not out_path.exists()
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named crossfoot.cli`

- [ ] **Step 4: Implement**

```python
# crossfoot/cli.py
import argparse
import getpass
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


def _cmd_parse(args):
    from crossfoot.doctypes.bank_statement.registry import _REGISTRY, classify
    from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
    from crossfoot.outcome import decide_outcome
    from crossfoot.pipeline.extract import BadPasswordError, extract
    from crossfoot.types import LayoutMatch

    password = None
    if args.password_prompt:
        password = getpass.getpass("PDF password: ")

    try:
        doc = extract(args.file, password=password)
    except BadPasswordError:
        print('{"outcome": "bad_password"}')
        return 1

    if doc.is_scanned:
        print('{"outcome": "scanned"}')
        return 0

    best, _runner_up = classify(doc)
    if best.score <= 0.5:
        print(f'{{"outcome": "unsupported", "best_score": {best.score}}}')
        return 0

    layout = next(l for l in _REGISTRY if l.slug == best.slug)
    parsed = layout.parse(doc)
    if parsed.opening_balance is None and parsed.transactions:
        first = parsed.transactions[0]
        parsed.opening_balance = first.balance + (first.debit or Decimal(0)) - (first.credit or Decimal(0))
    result = validate_bank_statement(parsed)
    outcome = decide_outcome(result, LayoutMatch(best.slug, best.score, trusted=True), structurally_clean=True)
    print(f'{{"outcome": "{outcome}", "layout": "{best.slug}", "rows": {len(parsed.transactions)}, '
          f'"failed_checks": {result.failed_checks}}}')
    return 0


def _cmd_redact(args):
    from crossfoot import redact as redact_module
    from crossfoot.config import get_settings
    from crossfoot.db import get_session
    from crossfoot.db.models import Document

    with get_session(get_settings().database_url) as session:
        doc_row = session.get(Document, args.document_id)
        if doc_row is None:
            print(f"no such document: {args.document_id}")
            return 1
        if doc_row.split == "holdout":
            print("refusing to redact a held-out document (never shared with any tool)")
            return 1
        storage_path = doc_row.storage_path

    if not storage_path:
        print(f"document {args.document_id} has no stored file")
        return 1

    redact_argv = [storage_path, args.out]
    if args.password_prompt:
        redact_argv.append("--password-prompt")
    try:
        redact_module.main(redact_argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _cmd_fixture(args):
    import json

    from crossfoot.config import get_settings
    from crossfoot.db import get_session
    from crossfoot.db.models import Correction

    with get_session(get_settings().database_url) as session:
        gold = session.query(Correction).filter_by(
            document_id=args.document_id, is_gold=True
        ).first()
        if gold is None:
            print(f"no gold correction for document {args.document_id}")
            return 1
        corrected_json = gold.corrected_json

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(corrected_json, indent=2, default=str))
    print(f"wrote {out_path}")
    return 0


def _cmd_synth(args):
    from crossfoot.synth import generate_statement_pdf

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = date(2026, 1, 1)
    for month in range(args.months):
        opening = Decimal("10000.00")
        rows = []
        running = opening
        for day in range(1, 6):
            debit = Decimal(random.randint(100, 5000))
            running -= debit
            rows.append((start + timedelta(days=day), f"UPI/DR/SYNTH{day}", debit, None))
        out_path = out_dir / f"{args.layout}_month{month + 1}.pdf"
        generate_statement_pdf(str(out_path), opening=opening, rows=rows, layout=args.layout)
        print(f"wrote {out_path}")
    return 0


def _cmd_regress(args):
    from crossfoot.pipeline.regress import run_regression
    run_regression(holdout=args.holdout)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="crossfoot")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse")
    p_parse.add_argument("file")
    p_parse.add_argument("--type", dest="document_type", default="bank_statement")
    p_parse.add_argument("--country", default="IN")
    p_parse.add_argument("--password-prompt", action="store_true")
    p_parse.set_defaults(func=_cmd_parse)

    p_redact = sub.add_parser("redact")
    p_redact.add_argument("document_id")
    p_redact.add_argument("--out", required=True)
    p_redact.add_argument("--password-prompt", action="store_true")
    p_redact.set_defaults(func=_cmd_redact)

    p_fixture = sub.add_parser("fixture")
    p_fixture.add_argument("document_id")
    p_fixture.add_argument("--out", required=True)
    p_fixture.set_defaults(func=_cmd_fixture)

    p_synth = sub.add_parser("synth")
    p_synth.add_argument("--layout", default="generic_v1")
    p_synth.add_argument("--months", type=int, default=1)
    p_synth.add_argument("--out", required=True)
    p_synth.set_defaults(func=_cmd_synth)

    p_regress = sub.add_parser("regress")
    p_regress.add_argument("--holdout", action="store_true")
    p_regress.set_defaults(func=_cmd_regress)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add crossfoot/cli.py crossfoot/redact.py crossfoot/terms.txt tests/test_cli.py pyproject.toml
git commit -m "feat: crossfoot CLI (parse, redact, fixture, synth, regress dispatch)"
```

---

### Task 20: Regression command (spec §9.3)

**Files:**
- Create: `crossfoot/pipeline/regress.py`
- Test: `tests/pipeline/test_regress.py`

**Interfaces:**
- Consumes: `Document`, `Parse`, `Correction`, `RegressionRun` models; `extract`, `classify`, `validate_bank_statement`, `decide_outcome`.
- Produces: `run_regression(holdout: bool = False) -> dict` — reparses every stored `Document` matching the split filter, compares outcome against the latest gold `Correction` when one exists, and writes a `RegressionRun` row with per-layout counts. Used by `crossfoot regress` (Task 19) and by CI-style local checks.
- **Row alignment note (spec §9.3):** full ordered-matching on `(date, amount, direction)` against gold rows is deferred past Phase 0-A's exit — Phase 0-A has zero gold corrections yet (no documents have been through the correction UI), so `run_regression` implements outcome-count and per-layout pass-rate metrics now, with the row-precision/recall comparison stubbed to return `None` until gold data exists in Phase 0-B. This is recorded as a known gap in the exit criteria below, not silently skipped.

- [ ] **Step 1: Write a failing test**

```python
# tests/pipeline/test_regress.py
import os

from crossfoot.config import Settings
from crossfoot.pipeline.regress import run_regression


def test_run_regression_returns_per_layout_counts():
    os.environ.setdefault("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    summary = run_regression(holdout=False)
    assert "documents_run" in summary
    assert "per_layout" in summary
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pipeline/test_regress.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# crossfoot/pipeline/regress.py
from collections import defaultdict

from sqlalchemy import select

from crossfoot.config import get_settings
from crossfoot.db import get_session
from crossfoot.db.models import Document, RegressionRun
from crossfoot.doctypes.bank_statement.registry import _REGISTRY, classify
from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
from crossfoot.outcome import decide_outcome
from crossfoot.pipeline.extract import BadPasswordError, extract
from crossfoot.types import LayoutMatch


def run_regression(holdout: bool = False) -> dict:
    settings = get_settings()
    split_filter = "holdout" if holdout else "train"
    per_layout = defaultdict(lambda: {"verified": 0, "document_inconsistent": 0,
                                       "parse_failed": 0, "other": 0})
    documents_run = 0

    with get_session(settings.database_url) as session:
        docs = session.scalars(
            select(Document).where(Document.split == split_filter, Document.deleted_at.is_(None))
        ).all()

        for doc in docs:
            if not doc.storage_path:
                continue
            documents_run += 1
            try:
                extracted = extract(doc.storage_path)
            except BadPasswordError:
                per_layout["_bad_password"]["other"] += 1
                continue

            if extracted.is_scanned:
                per_layout["_scanned"]["other"] += 1
                continue

            best, _runner_up = classify(extracted)
            if best.score <= 0.5:
                per_layout["_unsupported"]["other"] += 1
                continue

            layout = next(l for l in _REGISTRY if l.slug == best.slug)
            parsed = layout.parse(extracted)
            if parsed.opening_balance is None and parsed.transactions:
                first = parsed.transactions[0]
                parsed.opening_balance = first.balance + (first.debit or 0) - (first.credit or 0)
            result = validate_bank_statement(parsed)
            outcome = decide_outcome(
                result, LayoutMatch(best.slug, best.score, trusted=True), structurally_clean=True
            )
            bucket = per_layout[best.slug]
            bucket[outcome] = bucket.get(outcome, 0) + 1

        session.add(RegressionRun(
            parser_version="dirty", split=split_filter,
            documents_run=documents_run, per_layout=dict(per_layout),
        ))
        session.commit()

    return {"documents_run": documents_run, "per_layout": dict(per_layout), "row_metrics": None}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/pipeline/test_regress.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite once, end to end**

```bash
pytest -v
```

Expected: every test across all 20 tasks passes, including `tests/test_real_documents_e2e.py` against the five real files.

- [ ] **Step 6: Commit**

```bash
git add crossfoot/pipeline/regress.py tests/pipeline/test_regress.py
git commit -m "feat: regression command with per-layout outcome counts"
```

---

## Exit Criteria and Known Gaps (Phase 0-A)

**Met by this plan, if every task's Step 6/regression step passes:**
- Bank statement types, India country rules, validators, and outcome logic implemented verbatim from the Technical PRD, with the exact pytest cases §19 calls for.
- Three real layout families (SBI, HDFC, IDFC) parse their real training documents to `verified`.
- Playground upload/documents/detail pages and a `crossfoot` CLI (`parse`, `redact`, `fixture`, `synth`, `regress`) work locally, bound to `127.0.0.1`. **The correction workflow itself exists only as tested backend API endpoints** (`POST /documents/{id}/revalidate`, `POST /documents/{id}/corrections`, `POST /documents/{id}/corrections/{id}/gold`, exercised by `tests/test_corrections.py` per Task 18) — there is no browser editor UI wired to them. `document_detail.html` is a read-only dump (outcome, failed checks, raw JSON) with no form or JS calling these endpoints. A human wanting to record a correction today has to call the endpoints directly (e.g. via `curl`/`httpx`), not click through a page.

**Not met — explicitly out of scope for this plan, and why:**
- **No held-out set.** Product PRD §6.8's technical gate ("90%+ of held-out statements verified across 3+ layout families") cannot be scored yet: this session's owner decision was to use all 3 SBI files, the 1 HDFC file, and the 1 IDFC file as training data, with none reserved. **Before the technical gate can be scored, at least one additional month must be collected per bank and marked `split='holdout'` at upload — never opened, redacted, or shared with any tool.** Until then, none of the three layouts can be marked `trusted=True` in the database (the `matches`/`parse` code hardcodes `trusted=True` only inside test scaffolding and the CLI/app's own outcome call for demonstration; a real trust flag should be read from `layout_templates.trusted` once a Phase 0-B or later task wires that up).
- **`structurally_clean` is also hardcoded `True` everywhere** (`crossfoot/app.py`, `crossfoot/cli.py`, `crossfoot/pipeline/regress.py`, and `tests/test_real_documents_e2e.py` all call `decide_outcome(..., structurally_clean=True)` unconditionally), for the same demonstration-only reason as `trusted=True` above — but it is a separate signal and deserves its own callout, not just a footnote. Per Technical PRD §7, `structurally_clean` should mean "every amount matched the expected numeric format, every date parsed, no unassigned words inside the table area, row count per page matches" — i.e., a real, computed signal that every cell in the document parsed cleanly. None of the three layouts currently track this: they discover a bad amount or date cell and silently `continue`/skip it inside a `try`/`except`, so a genuine mis-parse leaves no trace for this flag to reflect. **The actual risk:** because `structurally_clean=True` is hardcoded, `decide_outcome` can currently route a real mis-parse into `document_inconsistent` (implying "the document itself has a real balance break, our parse is trustworthy") instead of the correct `parse_failed` (implying "our parse of this document cannot be trusted"), as long as the resulting row breaks number ≤3 (`MAX_LOCALIZED_BREAKS`) and no shape/date-ordering check also failed. `document_inconsistent` vs `parse_failed` is a trust-boundary distinction for a product whose entire promise is verified parsing, so this is not cosmetic. Properly fixing it requires reworking all three layouts' error handling to surface per-cell parse failures instead of discarding them, which is real follow-up work for Phase 0-B — do not compute this in-place without doing that rework first, or the flag will just be wrong in a different way.
- **No card bill layout was built against a real document** — none of the five provided PDFs is a card bill. Task 6 built the validator only, per spec §17 step 6/§19.
- **No invoice layouts** and **no UAE rules** — explicitly deferred per this plan's scope (Technical PRD §17 steps 7-8) until real samples of either exist.
- **Regression row-precision/recall against gold** (§9.3) is stubbed (`row_metrics: None`) because no document has been through the correction workflow yet to produce a gold correction. The first real use of the correction endpoints (`revalidate`/`corrections`/`corrections/{id}/gold`) on a live document — whether via a future browser editor or direct API calls — should be followed by a small follow-up task wiring up ordered (date, amount, direction) row matching.
- **SBI's own printed opening balance** (in unstructured dashboard-style header text, not the transaction table) is not parsed — Task 13 derives the opening balance from the first transaction row instead, which is validator-equivalent but doesn't cross-check the bank's own printed summary figure. Worth a follow-up once more SBI months arrive.

---

## Self-Review Notes

- **Spec coverage:** §3 types (Task 2), §4.3 country rules (Task 3), §5 pipeline (Tasks 9-11), §6.1/§6.2 validators (Tasks 5-6), §7 outcome (Task 7), §9.2 redaction (Task 19, folding in the existing script), §9.3 regression (Task 20), §10 schema — Phase-0 subset (Task 4), §14 playground pages/CLI (Tasks 16-19), §17 build order steps 1-6 (Tasks 1-20) all have an owning task. §17 steps 7 (invoices) and 8 (UAE) are explicitly deferred per this plan's stated scope, not silently dropped.
- **Placeholder scan:** no `TODO`/`TBD` left in any task's code; the one open-ended item (regression row-matching) is explicitly named as deferred with a stated reason and a concrete follow-up trigger, in the Exit Criteria section rather than inside a task.
- **Type consistency:** `Txn`, `ParsedStatement`, `CrossfootResult`, `RowBreak`, `LayoutMatch`, `Outcome` are defined once in Task 2 and referenced by identical names/fields in every later task; `Layout` protocol methods (`matches`, `parse`) match across the registry (Task 11) and all three real layouts (Tasks 12-14).
- **Review Focus coverage:** empty-cell dash handling is tested in Tasks 12-14's parse tests; two-digit-year dates and no-space balance suffixes are tested in Task 3; multi-line narration reassembly is tested in Task 10 (generic) and Tasks 13-14 (bank-specific); the fake-reference-number exclusion is implemented and exercised in Task 14's `_is_reference_noise`/end-to-end test (Task 15).
