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
