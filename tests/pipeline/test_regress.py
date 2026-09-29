import os

from sqlalchemy import text as sa_text

from crossfoot.config import get_settings
from crossfoot.pipeline.regress import run_regression


def test_run_regression_returns_per_layout_counts():
    os.environ.setdefault("CROSSFOOT_DATABASE_URL", "postgresql+psycopg://localhost/crossfoot")
    summary = run_regression(holdout=False)
    assert "documents_run" in summary
    assert "per_layout" in summary


def _insert_document(session, tenant_id, storage_path, sha256):
    from crossfoot.db.models import Document

    doc = Document(
        tenant_id=tenant_id, document_type="bank_statement", country="IN", currency="INR",
        split="train", source="self", owner_label="regress-test",
        sha256=sha256, storage_path=storage_path,
    )
    session.add(doc)
    session.flush()
    return doc


def test_run_regression_one_broken_document_does_not_abort_the_run(monkeypatch, capsys):
    """A single document's parse()/validate() exception must be counted, not
    let an unhandled exception crash the whole regression run and discard
    every other document's already-computed counts. It must also leave a
    diagnostic trace on stderr -- a bare counter increment isn't enough for
    an operator to find out *why* documents broke."""
    import crossfoot.doctypes.bank_statement.layouts  # noqa: F401  (register layouts)
    from crossfoot.db import get_session
    from crossfoot.doctypes.bank_statement.registry import _REGISTRY

    settings = get_settings()

    hdfc_layout = next(l for l in _REGISTRY if l.slug == "hdfc_savings_v1")

    def _boom(_doc):
        raise RuntimeError("simulated parser regression")

    monkeypatch.setattr(hdfc_layout, "parse", _boom)

    with get_session(settings.database_url) as session:
        tenant_id = session.execute(
            sa_text("select id from tenants where name='default'")
        ).scalar_one()
        pid = os.getpid()
        _insert_document(
            session, tenant_id, "data/SBI_1.pdf", f"regresstest-good-{pid}-{id(session)}"
        )
        _insert_document(
            session, tenant_id, "data/HDFC_1.pdf", f"regresstest-bad-{pid}-{id(session)}"
        )
        session.commit()

    summary = run_regression(holdout=False)

    # The good SBI document still gets counted as verified...
    assert summary["per_layout"]["sbi_savings_v1"]["verified"] >= 1
    # ...and the broken HDFC document is counted as an error, not a crash.
    assert summary["per_layout"]["hdfc_savings_v1"]["error"] >= 1
    # The run completed and wrote a total that includes both documents.
    assert summary["documents_run"] >= 2

    # The exception's type and message actually reached stderr, not just a
    # silent counter bump.
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "simulated parser regression" in captured.err
    assert "hdfc_savings_v1" in captured.err


def test_run_regression_missing_storage_file_is_counted_not_crashed(tmp_path, capsys):
    """A Document row whose storage_path file has since been deleted (or
    never existed) must not raise out of run_regression -- extract() itself
    can throw for reasons other than BadPasswordError, and a single stale
    row must not abort the whole run. The failure must also be traceable on
    stderr, not just reflected as an opaque counter increment."""
    from crossfoot.db import get_session

    settings = get_settings()
    missing_path = str(tmp_path / "this-file-does-not-exist.pdf")

    with get_session(settings.database_url) as session:
        tenant_id = session.execute(
            sa_text("select id from tenants where name='default'")
        ).scalar_one()
        pid = os.getpid()
        doc = _insert_document(
            session, tenant_id, missing_path, f"regresstest-missing-{pid}-{id(session)}"
        )
        session.commit()
        document_id = str(doc.id)

    summary = run_regression(holdout=False)  # must not raise

    assert summary["per_layout"]["_error"]["other"] >= 1
    assert summary["documents_run"] >= 1

    captured = capsys.readouterr()
    assert document_id in captured.err
    assert "FileNotFoundError" in captured.err
