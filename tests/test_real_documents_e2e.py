import glob

import pytest

# Import layouts to trigger registration
import crossfoot.doctypes.bank_statement.layouts  # noqa: F401

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
