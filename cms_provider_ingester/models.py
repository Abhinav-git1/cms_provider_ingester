# models.py
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    String, Integer, Float, DateTime, ForeignKey,
    UniqueConstraint, Index, Boolean, Text, func
)

class Base(DeclarativeBase):
    """SQLAlchemy Declarative base."""
    pass


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    npi: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    first_name: Mapped[str | None] = mapped_column(String(80))
    last_name: Mapped[str | None] = mapped_column(String(80))
    organization_name: Mapped[str | None] = mapped_column(String(160))

    taxonomy_code: Mapped[str | None] = mapped_column(String(20))
    taxonomy_desc: Mapped[str | None] = mapped_column(String(160))

    accepts_new_patients: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    website: Mapped[str | None] = mapped_column(String(256))

    # source-of-truth timestamp from upstream (if provided)
    source_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), index=True)

    # mark & sweep support
    last_seen_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), index=True)

    # audit
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    locations: Mapped[list["PracticeLocation"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_providers_name", "last_name", "first_name"),)


class PracticeLocation(Base):
    __tablename__ = "practice_locations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)

    address1: Mapped[str | None] = mapped_column(String(160))
    address2: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80), index=True)
    state: Mapped[str | None] = mapped_column(String(2), index=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), index=True)

    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(32))

    # mark & sweep support
    last_seen_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), index=True)

    provider: Mapped["Provider"] = relationship(back_populates="locations")

    __table_args__ = (
        UniqueConstraint("provider_id", "address1", "city", "state", "zip_code", name="uq_provider_address"),
        Index("ix_locations_city_state_zip", "city", "state", "zip_code"),
    )


class SyncState(Base):
    """Stores checkpoints for incremental/resumable syncs."""
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stream: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_page_cursor: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
