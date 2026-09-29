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
