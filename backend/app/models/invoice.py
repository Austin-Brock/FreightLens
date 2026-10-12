"""Carrier invoices and their charge lines."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import ACCESSORIAL_CODES, ChargeCode, InvoiceStatus


class CarrierInvoice(TimestampMixin, Base):
    """A freight bill as received from the carrier (EDI 210 / PDF / portal)."""

    __tablename__ = "carrier_invoices"
    __table_args__ = (
        UniqueConstraint("carrier_scac", "invoice_number", name="uq_invoice_carrier_number"),
        Index("ix_invoices_shipment", "shipment_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(64), index=True)
    carrier_scac: Mapped[str] = mapped_column(String(8), index=True)

    # The reference the carrier printed, kept verbatim even when it matches no
    # shipment — an unmatchable reference is itself an audit finding.
    shipment_ref: Mapped[str | None] = mapped_column(String(64))
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))

    invoice_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    status: Mapped[InvoiceStatus] = mapped_column(String(16), default=InvoiceStatus.RECEIVED)

    shipment: Mapped[Shipment | None] = relationship(back_populates="invoices")  # noqa: F821
    line_items: Mapped[list[InvoiceLineItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", lazy="selectin"
    )
    exceptions: Mapped[list[AuditException]] = relationship(  # noqa: F821
        back_populates="invoice", cascade="all, delete-orphan"
    )

    @property
    def line_total(self) -> Decimal:
        return sum((line.amount for line in self.line_items), Decimal("0"))

    def lines_for(self, charge_code: str) -> list[InvoiceLineItem]:
        return [line for line in self.line_items if line.charge_code == charge_code]

    def amount_for(self, charge_code: str) -> Decimal:
        return sum((line.amount for line in self.lines_for(charge_code)), Decimal("0"))

    @property
    def accessorial_lines(self) -> list[InvoiceLineItem]:
        return [line for line in self.line_items if line.charge_code in ACCESSORIAL_CODES]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CarrierInvoice {self.carrier_scac}/{self.invoice_number} {self.total_amount}>"


class InvoiceLineItem(Base):
    """One charge on a freight bill."""

    __tablename__ = "invoice_line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("carrier_invoices.id", ondelete="CASCADE"), index=True
    )
    line_number: Mapped[int] = mapped_column(default=1)
    charge_code: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(String(256))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1"))
    unit: Mapped[str | None] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    invoice: Mapped[CarrierInvoice] = relationship(back_populates="line_items")

    @property
    def is_accessorial(self) -> bool:
        return self.charge_code in ACCESSORIAL_CODES

    @property
    def is_linehaul(self) -> bool:
        return self.charge_code == ChargeCode.FREIGHT.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InvoiceLineItem {self.charge_code} {self.amount}>"
