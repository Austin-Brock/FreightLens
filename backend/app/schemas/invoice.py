"""Invoice request/response models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from app.models.enums import InvoiceStatus
from app.schemas.audit import AuditExceptionRead
from app.schemas.common import ORMModel
from app.schemas.shipment import ShipmentRead


class InvoiceLineItemBase(ORMModel):
    # Omit to have the server number the lines in the order supplied.
    line_number: int | None = None
    charge_code: str
    description: str | None = None
    quantity: Decimal = Decimal("1")
    unit: str | None = None
    amount: Decimal = Decimal("0")


class InvoiceLineItemRead(InvoiceLineItemBase):
    id: int
    line_number: int


class CarrierInvoiceBase(ORMModel):
    invoice_number: str = Field(max_length=64)
    carrier_scac: str = Field(max_length=8)
    shipment_ref: str | None = None
    invoice_date: date
    due_date: date | None = None
    currency: str = "USD"
    total_amount: Decimal = Decimal("0")


class CarrierInvoiceCreate(CarrierInvoiceBase):
    line_items: list[InvoiceLineItemBase] = Field(default_factory=list)


class CarrierInvoiceRead(CarrierInvoiceBase):
    id: int
    shipment_id: int | None = None
    status: InvoiceStatus
    line_items: list[InvoiceLineItemRead] = Field(default_factory=list)


class InvoiceStatusUpdate(ORMModel):
    status: InvoiceStatus


class InvoiceAuditRead(ORMModel):
    """One invoice, the shipment it belongs to, and what the audit found.

    The drill-down view needs all three at once; returning them together keeps
    the UI to a single request per invoice.
    """

    invoice: CarrierInvoiceRead
    shipment: ShipmentRead | None = None
    exceptions: list[AuditExceptionRead] = Field(default_factory=list)
    billed_total: Decimal
    expected_total: Decimal = Field(
        description="Billed total less the variance the audit attributed to it."
    )
    variance_total: Decimal
