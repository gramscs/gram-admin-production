"""Private bookkeeping for centralized LR issuance (migration 005)."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import Boolean, Integer, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from database.models import JSON_DATA, UTCDateTime

class LRBase(DeclarativeBase):
    pass

class IssuingLocation(LRBase):
    __tablename__ = 'issuing_locations'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    location_name: Mapped[str] = mapped_column(String(120))
    location_code: Mapped[str] = mapped_column(String(10), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reconciliation_digest: Mapped[str | None] = mapped_column(String(64))
    reconciliation_reason: Mapped[str | None] = mapped_column(Text)
    opening_counters: Mapped[dict] = mapped_column(JSON_DATA, server_default='{}')
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

class LRSequence(LRBase):
    __tablename__ = 'lr_sequences'
    location_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    financial_year: Mapped[str] = mapped_column(String(4), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

class LRRequest(LRBase):
    __tablename__ = 'lr_requests'
    actor_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    request_key: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON_DATA)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

class LRImport(LRBase):
    __tablename__ = 'lr_imports'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    digest: Mapped[str] = mapped_column(String(64))
    rows: Mapped[list] = mapped_column(JSON_DATA)
    response: Mapped[dict | None] = mapped_column(JSON_DATA)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

class LRAudit(LRBase):
    __tablename__ = 'lr_audit_log'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid)
    lr_number: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    before_values: Mapped[dict | None] = mapped_column(JSON_DATA)
    after_values: Mapped[dict] = mapped_column(JSON_DATA)
    reason: Mapped[str | None] = mapped_column(Text)
