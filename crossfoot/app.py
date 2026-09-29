import hashlib
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from crossfoot.config import Settings, get_settings
from crossfoot.db import get_session
from crossfoot.db.models import Document, Parse

# Import layouts to trigger registration with the registry (side-effecting import).
import crossfoot.doctypes.bank_statement.layouts  # noqa: F401
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

    @app.get("/documents/{document_id}")
    def document_detail(document_id: str):
        return {"document_id": document_id}

    return app


app = create_app()
