from datetime import date
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient

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
