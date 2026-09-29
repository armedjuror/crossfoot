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
