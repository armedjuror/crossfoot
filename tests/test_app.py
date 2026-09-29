import hashlib
from datetime import date
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import text

from crossfoot.app import create_app
from crossfoot.config import Settings
from crossfoot.synth import generate_statement_pdf


def _client(tmp_path, db_url, monkeypatch):
    import crossfoot.app as app_module

    # Redirect upload storage into tmp_path so tests never pollute the real
    # data/ directory that tests/test_real_documents_e2e.py globs over.
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    settings = Settings(database_url=db_url, mode="playground")
    return TestClient(create_app(settings))


def test_get_upload_page(tmp_path, monkeypatch):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url, monkeypatch)
    response = client.get("/upload")
    assert response.status_code == 200
    assert b"Upload" in response.content


def test_post_upload_creates_a_document_and_a_parse(tmp_path, monkeypatch):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url, monkeypatch)
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


def _make_document(db_url, split, owner_label):
    import uuid

    from crossfoot.db import get_session
    from crossfoot.db.models import Document

    with get_session(db_url) as session:
        tenant_id = session.execute(
            text("select id from tenants where name = 'default'")
        ).scalar_one()
        # Include a fresh uuid so repeated test runs against a persistent
        # database never collide on the (tenant_id, sha256) unique index.
        unique_payload = f"{owner_label}-{uuid.uuid4()}"
        doc = Document(
            tenant_id=tenant_id, document_type="bank_statement", country="IN",
            currency="INR", split=split, source="self", owner_label=owner_label,
            sha256=hashlib.sha256(unique_payload.encode()).hexdigest(),
        )
        session.add(doc)
        session.commit()
        return doc.id


def test_documents_list_hides_holdout_by_default(tmp_path, monkeypatch):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url, monkeypatch)
    holdout_id = _make_document(db_url, "holdout", "holdout-owner-hide-test")
    response = client.get("/documents")
    assert response.status_code == 200
    assert str(holdout_id) not in response.text


def test_documents_list_shows_holdout_when_requested(tmp_path, monkeypatch):
    import os
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url, monkeypatch)
    holdout_id = _make_document(db_url, "holdout", "holdout-owner-show-test")
    response = client.get("/documents?show_holdout=true")
    assert response.status_code == 200
    assert str(holdout_id) in response.text


def test_document_detail_shows_latest_parse_outcome(tmp_path, monkeypatch):
    import os
    from crossfoot.db import get_session
    from crossfoot.db.models import Parse

    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    client = _client(tmp_path, db_url, monkeypatch)
    doc_id = _make_document(db_url, "train", "detail-latest-parse-test")
    with get_session(db_url) as session:
        tenant_id = session.execute(
            text("select id from tenants where name = 'default'")
        ).scalar_one()
        session.add(Parse(tenant_id=tenant_id, document_id=doc_id,
                           parser_version="dirty", outcome="parse_failed"))
        session.commit()
        session.add(Parse(tenant_id=tenant_id, document_id=doc_id,
                           parser_version="dirty", outcome="verified"))
        session.commit()
    response = client.get(f"/documents/{doc_id}")
    assert response.status_code == 200
    assert b"verified" in response.content
    assert b"parse_failed" not in response.content
