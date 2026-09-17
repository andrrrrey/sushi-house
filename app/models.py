from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
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
