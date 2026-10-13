"""ORM tables: invoices and attempts. There is deliberately no token column anywhere."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("env", "erp_invoice_no", name="uq_invoices_env_erp_invoice_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env: Mapped[str] = mapped_column(String(4), index=True, nullable=False)  # "dev" | "prd"
    erp_invoice_no: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    tracking_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    seller_ntn: Mapped[str] = mapped_column(String(13), index=True, nullable=False)
    callback_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fbr_invoice_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dated: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pral_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pral_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    local_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    attempt_rows: Mapped[list["Attempt"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", lazy="noload"
    )


class Attempt(Base):
    """One HTTP call to PRAL. Request body is the exact JSON sent; headers are never stored."""

    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    tracking_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    call: Mapped[str] = mapped_column(String(10), nullable=False)  # "validate" | "post"
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(255), nullable=False)
    request_body: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="attempt_rows")
