import hashlib
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from crossfoot.config import Settings, get_settings
from crossfoot.db import get_session
from crossfoot.db.models import Correction, Document, Parse

# Import layouts to trigger registration with the registry (side-effecting import).
import crossfoot.doctypes.bank_statement.layouts  # noqa: F401
from crossfoot.doctypes.bank_statement.registry import _REGISTRY, classify
from crossfoot.doctypes.bank_statement.validator import validate_bank_statement
from crossfoot.outcome import decide_outcome
from crossfoot.pipeline.extract import BadPasswordError, extract
from crossfoot.types import LayoutMatch

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
DATA_DIR = Path("data")


def _statement_from_payload(payload: dict):
    """Build a ParsedStatement from a corrected-transaction JSON payload.

    Amounts always go through Decimal(str(v)) rather than Decimal(v) directly:
    a payload amount may arrive as a JSON number (parsed to a Python float by
    the standard json module) rather than a quoted string, and Decimal(float)
    would silently bake in binary floating-point error (e.g. Decimal(1000.1)
    != Decimal("1000.1")). Routing every value through str() first normalizes
    both cases onto the same exact-decimal path.
    """
    from datetime import date as date_cls
    from decimal import Decimal

    from crossfoot.types import ParsedStatement, Txn

    def _dec(v):
        return Decimal(str(v)) if v is not None else None

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
                text("select id from tenants where name = 'default'")
            ).scalar_one()
            doc_row = Document(
                tenant_id=tenant_id, document_type=document_type, country=country,
                currency="INR", split=split, source=source, owner_label=owner_label,
                consent_note=consent_note, storage_path=str(storage_path), sha256=sha256,
            )
            session.add(doc_row)
            session.flush()
            document_id = doc_row.id

            try:
                extracted = extract(str(storage_path), password=password)
            except BadPasswordError:
                parse_row = Parse(tenant_id=tenant_id, document_id=document_id,
                                   parser_version="dirty", outcome="bad_password")
                session.add(parse_row)
                session.commit()
                return RedirectResponse(f"/documents/{document_id}", status_code=303)

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
                tenant_id=tenant_id, document_id=document_id, parser_version="dirty",
                layout_slug=layout_slug, layout_score=layout_score, outcome=outcome,
                crossfoot_passed=(outcome == "verified"), failed_checks=failed_checks,
                output_json=output_json,
            )
            session.add(parse_row)
            session.commit()

        return RedirectResponse(f"/documents/{document_id}", status_code=303)

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

    @app.post("/documents/{document_id}/revalidate")
    def revalidate(document_id: str, payload: dict):
        # Dry run only: parses the payload and runs the crossfoot checks, but
        # never opens a session or writes a Correction/Parse row.
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
        # crossfoot_passed is always computed here from validate_bank_statement,
        # never taken from a client-supplied field in payload.
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
        with get_session(settings.database_url) as session:
            target = session.get(Correction, correction_id)
            if target is None:
                return {"error": "correction not found"}
            if not target.crossfoot_passed:
                # The DB has a check constraint (NOT is_gold OR crossfoot_passed)
                # that would reject this at commit time anyway; we short-circuit
                # here so the client gets a clean response instead of a 500 from
                # a bubbled-up IntegrityError.
                return {"error": "cannot mark a failing correction as gold"}
            # Unset any prior gold for this document and set this one gold in
            # the same transaction/session, so a crash between the two writes
            # can't leave two golds (violates one_gold_per_document) or zero.
            session.query(Correction).filter_by(document_id=document_id, is_gold=True).update(
                {"is_gold": False}
            )
            target.is_gold = True
            session.commit()
            return {"id": str(target.id), "is_gold": True}

    return app


app = create_app()
