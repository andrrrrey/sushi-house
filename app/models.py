from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IntegrationSetting(Base):
    __tablename__ = "integration_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MangoCallEvent(Base):
    __tablename__ = "mango_call_events"
    __table_args__ = (UniqueConstraint("call_id", "sequence", name="uq_mango_call_event_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(128), index=True, default="")
    call_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    call_state: Mapped[str] = mapped_column(String(40), index=True)
    location: Mapped[str] = mapped_column(String(40), default="")
    from_number_encrypted: Mapped[str] = mapped_column(Text, default="")
    to_number_encrypted: Mapped[str] = mapped_column(Text, default="")
    to_extension: Mapped[str] = mapped_column(String(40), default="")
    line_number: Mapped[str] = mapped_column(String(160), default="")
    disconnect_reason: Mapped[str] = mapped_column(String(40), default="")
    event_timestamp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class TestCall(Base):
    __tablename__ = "test_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    phone_encrypted: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_sheet: Mapped[str] = mapped_column(String(120), default="")
    source_rows: Mapped[str] = mapped_column(String(40), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class InboundCall(Base):
    __tablename__ = "inbound_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="connected")
    phone_encrypted: Mapped[str] = mapped_column(Text, default="")
    customer_name: Mapped[str] = mapped_column(String(160), default="")
    intent: Mapped[str] = mapped_column(String(40), default="new_order", index=True)
    service_type: Mapped[str] = mapped_column(String(24), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    draft_order: Mapped[str] = mapped_column(Text, default="{}")
    address: Mapped[str] = mapped_column(Text, default="")
    payment_method: Mapped[str] = mapped_column(String(80), default="")
    result: Mapped[str] = mapped_column(String(80), default="draft")
    transfer_reason: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
