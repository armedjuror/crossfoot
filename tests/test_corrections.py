import os
import uuid
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
        # The brief's original snippet seeded sha256 with id(session) (a
        # CPython memory address), which is not guaranteed unique: once a
        # Session is garbage collected its address can be reused immediately
        # by the next Session, producing a duplicate sha256 and tripping
        # documents_tenant_sha256_idx. This was not hypothetical -- calling
        # _make_document twice in the same test (needed for the cross-document
        # mark_gold regression test) reproduced the collision. uuid4 has no
        # such reuse risk.
        doc = Document(tenant_id=tenant_id, document_type="bank_statement", country="IN",
                        currency="INR", split="train", source="self", owner_label="test",
                        sha256="deadbeef" + uuid.uuid4().hex)
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


def test_mark_gold_rejects_correction_from_a_different_document():
    client, db_url = _client()
    doc_a = _make_document(db_url)
    doc_b = _make_document(db_url)
    payload = {
        "opening_balance": "1000.00",
        "transactions": [
            {"date": "2026-09-01", "narration": "x", "reference": None,
             "debit": "100.00", "credit": None, "balance": "900.00", "page": 0}
        ],
        "edit_count": 1,
    }
    response = client.post(f"/documents/{doc_b}/corrections", json=payload)
    assert response.status_code == 200
    correction_id = response.json()["id"]

    # Wrong document in the URL (doc_a), but a real correction_id that
    # belongs to doc_b. Must be rejected, not silently applied to doc_a
    # and not a 500 from the one_gold_per_document unique index or the
    # gold/passed check constraint.
    cross_doc_response = client.post(f"/documents/{doc_a}/corrections/{correction_id}/gold")
    assert cross_doc_response.status_code == 200
    assert "error" in cross_doc_response.json()

    with get_session(db_url) as session:
        from crossfoot.db.models import Correction
        corr = session.get(Correction, correction_id)
        assert corr.is_gold is False
