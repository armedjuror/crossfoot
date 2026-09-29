import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('bank_statement','credit_card_bill','invoice')",
            name="documents_document_type_check",
        ),
        CheckConstraint("split IN ('train','holdout')", name="documents_split_check"),
        CheckConstraint(
            "source IN ('self','family_friend','concierge')", name="documents_source_check"
        ),
        CheckConstraint(
            "source = 'self' OR consent_note IS NOT NULL", name="documents_consent_check"
        ),
        UniqueConstraint("id", "tenant_id", name="documents_id_tenant_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    document_type: Mapped[str]
    country: Mapped[str]
    currency: Mapped[str]
    split: Mapped[str]
    source: Mapped[str]
    owner_label: Mapped[str]
    consent_note: Mapped[str | None]
    storage_path: Mapped[str | None]
    sha256: Mapped[str]
    page_count: Mapped[int | None]
    is_scanned: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "documents_tenant_sha256_idx", Document.tenant_id, Document.sha256,
    unique=True, postgresql_where=Document.deleted_at.is_(None),
)


class Parse(Base):
    __tablename__ = "parses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"]
        ),
        CheckConstraint(
            "outcome IN ('verified','document_inconsistent','parse_failed',"
            "'unsupported','scanned','bad_password','error','needs_review')",
            name="parses_outcome_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    parser_version: Mapped[str]
    layout_slug: Mapped[str | None]
    layout_score: Mapped[float | None]
    runner_up_score: Mapped[float | None]
    outcome: Mapped[str]
    crossfoot_passed: Mapped[bool | None]
    failed_checks: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'")
    )
    used_llm_fallback: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


Index("parses_document_created_idx", Parse.document_id, Parse.created_at.desc())


class Correction(Base):
    __tablename__ = "corrections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"]
        ),
        CheckConstraint(
            "NOT is_gold OR crossfoot_passed", name="corrections_gold_passed_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    base_parse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parses.id"))
    corrected_json: Mapped[dict] = mapped_column(JSONB)
    crossfoot_passed: Mapped[bool]
    is_gold: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    edit_count: Mapped[int]
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


Index(
    "one_gold_per_document", Correction.document_id, unique=True,
    postgresql_where=Correction.is_gold.is_(True),
)


class LayoutTemplate(Base):
    __tablename__ = "layout_templates"

    slug: Mapped[str] = mapped_column(primary_key=True)
    document_type: Mapped[str]
    country: Mapped[str]
    definition: Mapped[dict] = mapped_column(JSONB)
    source_parse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parses.id"))
    validated_successes: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    active: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    trusted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RegressionRun(Base):
    __tablename__ = "regression_runs"
    __table_args__ = (
        CheckConstraint("split IN ('train','holdout','all')", name="regression_runs_split_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    parser_version: Mapped[str]
    split: Mapped[str]
    documents_run: Mapped[int]
    per_layout: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
