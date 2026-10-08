"""Audit runs and the exceptions they produce."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import AuditRunStatus, ExceptionSeverity, ExceptionStatus


class AuditRun(Base):
    """One execution of the audit engine over a set of invoices."""

    __tablename__ = "audit_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[AuditRunStatus] = mapped_column(String(16), default=AuditRunStatus.RUNNING)
    triggered_by: Mapped[str] = mapped_column(String(64), default="api")

    invoices_audited: Mapped[int] = mapped_column(default=0)
    exceptions_found: Mapped[int] = mapped_column(default=0)
    total_billed: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    total_variance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text)

    exceptions: Mapped[list["AuditException"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditRun {self.id} {self.status} exceptions={self.exceptions_found}>"


class AuditException(TimestampMixin, Base):
    """A single discrepancy between what was billed and what was contracted.

    ``variance_amount`` is positive when the carrier overbilled (money the
    shipper can recover) and negative when they underbilled.
    """

    __tablename__ = "audit_exceptions"
    __table_args__ = (
        UniqueConstraint("run_id", "invoice_id", "rule_code", "charge_code", name="uq_exception_key"),
        Index("ix_exceptions_rule_status", "rule_code", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"))
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("carrier_invoices.id", ondelete="CASCADE"), index=True
    )
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))

    rule_code: Mapped[str] = mapped_column(String(48), index=True)
    rule_name: Mapped[str] = mapped_column(String(128))
    severity: Mapped[ExceptionSeverity] = mapped_column(String(16), default=ExceptionSeverity.MEDIUM)
    status: Mapped[ExceptionStatus] = mapped_column(String(16), default=ExceptionStatus.OPEN)
    charge_code: Mapped[str] = mapped_column(String(32), default="")

    billed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    variance_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    message: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(Text)

    run: Mapped[AuditRun | None] = relationship(back_populates="exceptions")
    invoice: Mapped["CarrierInvoice | None"] = relationship(back_populates="exceptions")  # noqa: F821
    shipment: Mapped["Shipment | None"] = relationship(back_populates="exceptions")  # noqa: F821

    # Denormalised for display: an exception list is always read alongside
    # "which invoice, which carrier", and resolving it here keeps the API
    # response flat instead of making the client join.
    @property
    def invoice_number(self) -> str | None:
        return self.invoice.invoice_number if self.invoice else None

    @property
    def carrier_scac(self) -> str | None:
        return self.invoice.carrier_scac if self.invoice else None

    @property
    def shipment_reference(self) -> str | None:
        if self.shipment is not None:
            return self.shipment.shipment_id
        return self.invoice.shipment_ref if self.invoice else None

    @property
    def is_recoverable(self) -> bool:
        return self.variance_amount > 0 and self.status in {
            ExceptionStatus.OPEN,
            ExceptionStatus.DISPUTED,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditException {self.rule_code} variance={self.variance_amount}>"
