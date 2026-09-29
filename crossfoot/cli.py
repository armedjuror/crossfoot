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
            print("refusing to redact a holdout document (never shared with any tool)")
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
    import json

    from crossfoot.pipeline.regress import run_regression
    summary = run_regression(holdout=args.holdout)
    print(json.dumps(summary, indent=2, default=str))
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
