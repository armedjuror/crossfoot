import sys
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
            except Exception as exc:
                # e.g. a stale Document row whose storage_path file has since
                # been deleted, or a corrupt PDF. One bad document must not
                # abort the whole run and discard every other document's
                # already-computed counts. Print to stderr so the counter
                # increment isn't the only trace of what broke -- an operator
                # staring at "_error": 15 needs to know why, not just that.
                print(
                    f"regress: {doc.id} failed during extract: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                per_layout["_error"]["other"] += 1
                continue

            if extracted.is_scanned:
                per_layout["_scanned"]["other"] += 1
                continue

            best, _runner_up = classify(extracted)
            if best.score <= 0.5:
                per_layout["_unsupported"]["other"] += 1
                continue

            # A single document's parser bugging out (a stale layout template,
            # an edge-case row shape, a real regression the parser code just
            # introduced) must not abort the run and discard every other
            # document's already-computed counts. This command's whole job is
            # to surface breakage across the corpus, so a broken document is
            # itself a result worth counting ("_error"/"error"), not a crash.
            try:
                layout = next(l for l in _REGISTRY if l.slug == best.slug)
                parsed = layout.parse(extracted)
                if parsed.opening_balance is None and parsed.transactions:
                    first = parsed.transactions[0]
                    parsed.opening_balance = (
                        first.balance + (first.debit or 0) - (first.credit or 0)
                    )
                result = validate_bank_statement(parsed)
                outcome = decide_outcome(
                    result, LayoutMatch(best.slug, best.score, trusted=True),
                    structurally_clean=True,
                )
            except Exception as exc:
                print(
                    f"regress: {doc.id} failed during parse/validate "
                    f"(layout={best.slug}): {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                bucket = per_layout[best.slug]
                bucket["error"] = bucket.get("error", 0) + 1
                continue

            bucket = per_layout[best.slug]
            bucket[outcome] = bucket.get(outcome, 0) + 1

        session.add(RegressionRun(
            parser_version="dirty", split=split_filter,
            documents_run=documents_run, per_layout=dict(per_layout),
        ))
        session.commit()

    return {"documents_run": documents_run, "per_layout": dict(per_layout), "row_metrics": None}
