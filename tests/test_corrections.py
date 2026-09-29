import os
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from crossfoot.app import create_app
from crossfoot.config import Settings
from crossfoot.db import get_session
from crossfoot.db.models import Document
from sqlalchemy import text as sa_text


def _client():
    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    return TestClient(create_app(Settings(database_url=db_url, mode="playground"))), db_url


def _make_document(db_url):
    with get_session(db_url) as session:
        tenant_id = session.execute(sa_text("select id from tenants where name='default'")).scalar_one()
        doc = Document(tenant_id=tenant_id, document_type="bank_statement", country="IN",
                        currency="INR", split="train", source="self", owner_label="test",
                        sha256="deadbeef" + str(id(session)))
        session.add(doc)
        session.commit()
        return str(doc.id)


def test_revalidate_does_not_persist_a_correction():
    client, db_url = _client()
    doc_id = _make_document(db_url)
    payload = {
        "opening_balance": "1000.00",
        "transactions": [
            {"date": "2026-09-01", "narration": "x", "reference": None,
             "debit": "100.00", "credit": None, "balance": "900.00", "page": 0}
        ],
    }
    response = client.post(f"/documents/{doc_id}/revalidate", json=payload)
    assert response.status_code == 200
    assert response.json()["passed"] is True

    with get_session(db_url) as session:
        from crossfoot.db.models import Correction
        assert session.query(Correction).filter_by(document_id=doc_id).count() == 0


def test_save_correction_then_mark_gold():
    client, db_url = _client()
    doc_id = _make_document(db_url)
    payload = {
        "opening_balance": "1000.00",
        "transactions": [
            {"date": "2026-09-01", "narration": "x", "reference": None,
             "debit": "100.00", "credit": None, "balance": "900.00", "page": 0}
        ],
        "edit_count": 1,
    }
    response = client.post(f"/documents/{doc_id}/corrections", json=payload)
    assert response.status_code == 200
    correction_id = response.json()["id"]

    gold_response = client.post(f"/documents/{doc_id}/corrections/{correction_id}/gold")
    assert gold_response.status_code == 200

    with get_session(db_url) as session:
        from crossfoot.db.models import Correction
        golds = session.query(Correction).filter_by(document_id=doc_id, is_gold=True).all()
        assert len(golds) == 1
        assert str(golds[0].id) == correction_id
