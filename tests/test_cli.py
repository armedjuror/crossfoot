import subprocess
import sys
from datetime import date
from decimal import Decimal

from crossfoot.synth import generate_statement_pdf


def test_parse_command_prints_outcome(tmp_path):
    pdf_path = tmp_path / "synth.pdf"
    generate_statement_pdf(str(pdf_path), opening=Decimal("1000.00"), rows=[
        (date(2026, 9, 1), "UPI/DR/GROCERY", Decimal("100.00"), None),
    ])
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "parse", str(pdf_path),
         "--type", "bank_statement", "--country", "IN"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "outcome" in result.stdout.lower()


def test_synth_command_writes_a_pdf(tmp_path):
    out_dir = tmp_path / "pdfs"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "synth", "--layout", "generic_v1",
         "--months", "1", "--out", str(out_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert list(out_dir.glob("*.pdf"))


def test_fixture_command_writes_gold_correction_json(tmp_path):
    import json
    import os
    from sqlalchemy import text as sa_text

    from crossfoot.config import get_settings
    from crossfoot.db import get_session
    from crossfoot.db.models import Correction, Document

    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", get_settings().database_url)
    with get_session(db_url) as session:
        tenant_id = session.execute(sa_text("select id from tenants where name='default'")).scalar_one()
        doc = Document(
            tenant_id=tenant_id, document_type="bank_statement", country="IN", currency="INR",
            split="train", source="self", owner_label="fixture-test",
            sha256=f"fixturetest{os.getpid()}{id(session)}",
        )
        session.add(doc)
        session.flush()
        gold = Correction(
            tenant_id=tenant_id, document_id=doc.id,
            corrected_json={"transactions": [], "closing_balance": "0.00"},
            crossfoot_passed=True, is_gold=True, edit_count=1,
        )
        session.add(gold)
        session.commit()
        document_id = str(doc.id)

    out_path = tmp_path / "fixture.json"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "fixture", document_id, "--out", str(out_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    written = json.loads(out_path.read_text())
    assert written == {"transactions": [], "closing_balance": "0.00"}


def test_fixture_command_fails_cleanly_with_no_gold_correction(tmp_path):
    out_path = tmp_path / "fixture.json"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "fixture", "00000000-0000-0000-0000-000000000000",
         "--out", str(out_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert not out_path.exists()


def test_redact_command_refuses_holdout_document(tmp_path):
    import os
    from sqlalchemy import text as sa_text

    from crossfoot.config import get_settings
    from crossfoot.db import get_session
    from crossfoot.db.models import Document

    db_url = os.environ.get("CROSSFOOT_DATABASE_URL", get_settings().database_url)
    with get_session(db_url) as session:
        tenant_id = session.execute(sa_text("select id from tenants where name='default'")).scalar_one()
        doc = Document(
            tenant_id=tenant_id, document_type="bank_statement", country="IN", currency="INR",
            split="holdout", source="self", owner_label="redact-test",
            sha256=f"redacttest{os.getpid()}{id(session)}", storage_path=str(tmp_path / "irrelevant.pdf"),
        )
        session.add(doc)
        session.commit()
        document_id = str(doc.id)

    out_path = tmp_path / "redacted.pdf"
    result = subprocess.run(
        [sys.executable, "-m", "crossfoot.cli", "redact", document_id, "--out", str(out_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "holdout" in result.stdout.lower()
    assert not out_path.exists()
