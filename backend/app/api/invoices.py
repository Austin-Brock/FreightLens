"""Carrier invoice endpoints."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, PageParams
from app.models import CarrierInvoice, InvoiceLineItem, Shipment
from app.schemas import (
    AuditExceptionRead,
    CarrierInvoiceCreate,
    CarrierInvoiceRead,
    InvoiceAuditRead,
    InvoiceStatusUpdate,
    Page,
)
from app.schemas.shipment import ShipmentRead
from app.services.rules import MONETARY_RULES

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=Page[CarrierInvoiceRead])
def list_invoices(
    db: DbSession,
    page: PageParams,
    carrier_scac: str | None = None,
    invoice_status: str | None = Query(None, alias="status"),
    invoiced_from: date | None = None,
    invoiced_to: date | None = None,
    unmatched_only: bool = Query(False, description="Only invoices with no shipment match."),
) -> Page[CarrierInvoiceRead]:
    stmt = select(CarrierInvoice).options(selectinload(CarrierInvoice.line_items))
    if carrier_scac:
        stmt = stmt.where(CarrierInvoice.carrier_scac == carrier_scac.upper())
    if invoice_status:
        stmt = stmt.where(CarrierInvoice.status == invoice_status.lower())
    if invoiced_from:
        stmt = stmt.where(CarrierInvoice.invoice_date >= invoiced_from)
    if invoiced_to:
        stmt = stmt.where(CarrierInvoice.invoice_date <= invoiced_to)
    if unmatched_only:
        stmt = stmt.where(CarrierInvoice.shipment_id.is_(None))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(CarrierInvoice.invoice_date.desc(), CarrierInvoice.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[CarrierInvoiceRead](
        items=[CarrierInvoiceRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


def _require_invoice(db, invoice_id: int) -> CarrierInvoice:
    invoice = db.get(CarrierInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invoice {invoice_id} not found")
    return invoice


@router.get("/{invoice_id}", response_model=CarrierInvoiceRead)
def get_invoice(invoice_id: int, db: DbSession) -> CarrierInvoiceRead:
    return CarrierInvoiceRead.model_validate(_require_invoice(db, invoice_id))


@router.get("/{invoice_id}/exceptions", response_model=list[AuditExceptionRead])
def invoice_exceptions(invoice_id: int, db: DbSession) -> list[AuditExceptionRead]:
    invoice = _require_invoice(db, invoice_id)
    return [AuditExceptionRead.model_validate(exc) for exc in invoice.exceptions]


@router.get("/{invoice_id}/audit", response_model=InvoiceAuditRead)
def invoice_audit(invoice_id: int, db: DbSession) -> InvoiceAuditRead:
    """Shipment + invoice + findings — everything the drill-down view shows."""
    invoice = _require_invoice(db, invoice_id)
    exceptions = list(invoice.exceptions)
    variance = sum(
        (exc.variance_amount for exc in exceptions if exc.rule_code in MONETARY_RULES),
        Decimal("0"),
    )
    return InvoiceAuditRead(
        invoice=CarrierInvoiceRead.model_validate(invoice),
        shipment=(
            ShipmentRead.model_validate(invoice.shipment) if invoice.shipment else None
        ),
        exceptions=[AuditExceptionRead.model_validate(exc) for exc in exceptions],
        billed_total=invoice.total_amount,
        expected_total=invoice.total_amount - variance,
        variance_total=variance,
    )


@router.post("", response_model=CarrierInvoiceRead, status_code=status.HTTP_201_CREATED)
def create_invoice(payload: CarrierInvoiceCreate, db: DbSession) -> CarrierInvoiceRead:
    if db.scalar(
        select(CarrierInvoice).where(
            CarrierInvoice.carrier_scac == payload.carrier_scac.upper(),
            CarrierInvoice.invoice_number == payload.invoice_number,
        )
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Invoice {payload.carrier_scac}/{payload.invoice_number} already exists",
        )

    data = payload.model_dump(exclude={"line_items"})
    invoice = CarrierInvoice(**data)
    for index, line in enumerate(payload.line_items, start=1):
        invoice.line_items.append(
            InvoiceLineItem(**{**line.model_dump(), "line_number": line.line_number or index})
        )
    if not payload.total_amount:
        invoice.total_amount = invoice.line_total
    if invoice.shipment_ref:
        invoice.shipment_id = db.scalar(
            select(Shipment.id).where(Shipment.shipment_id == invoice.shipment_ref)
        )

    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return CarrierInvoiceRead.model_validate(invoice)


@router.patch("/{invoice_id}/status", response_model=CarrierInvoiceRead)
def update_status(
    invoice_id: int, payload: InvoiceStatusUpdate, db: DbSession
) -> CarrierInvoiceRead:
    invoice = _require_invoice(db, invoice_id)
    invoice.status = payload.status
    db.commit()
    db.refresh(invoice)
    return CarrierInvoiceRead.model_validate(invoice)
