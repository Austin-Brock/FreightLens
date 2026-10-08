"""Shipment endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DbSession, PageParams
from app.models import Shipment
from app.schemas import Page, ShipmentCreate, ShipmentRead

router = APIRouter(prefix="/shipments", tags=["shipments"])


@router.get("", response_model=Page[ShipmentRead])
def list_shipments(
    db: DbSession,
    page: PageParams,
    carrier_scac: str | None = None,
    mode: str | None = None,
    origin_state: str | None = None,
    dest_state: str | None = None,
    shipped_from: date | None = None,
    shipped_to: date | None = None,
    late_only: bool = Query(False, description="Only shipments delivered after the promised date."),
) -> Page[ShipmentRead]:
    stmt = select(Shipment)
    if carrier_scac:
        stmt = stmt.where(Shipment.carrier_scac == carrier_scac.upper())
    if mode:
        stmt = stmt.where(Shipment.mode == mode.lower())
    if origin_state:
        stmt = stmt.where(Shipment.origin_state == origin_state.upper())
    if dest_state:
        stmt = stmt.where(Shipment.dest_state == dest_state.upper())
    if shipped_from:
        stmt = stmt.where(Shipment.ship_date >= shipped_from)
    if shipped_to:
        stmt = stmt.where(Shipment.ship_date <= shipped_to)
    if late_only:
        stmt = stmt.where(
            Shipment.promised_date.is_not(None),
            Shipment.delivery_date.is_not(None),
            Shipment.delivery_date > Shipment.promised_date,
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Shipment.ship_date.desc(), Shipment.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[ShipmentRead](
        items=[ShipmentRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{shipment_id}", response_model=ShipmentRead)
def get_shipment(shipment_id: str, db: DbSession) -> ShipmentRead:
    shipment = db.scalar(select(Shipment).where(Shipment.shipment_id == shipment_id))
    if shipment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Shipment {shipment_id} not found")
    return ShipmentRead.model_validate(shipment)


@router.post("", response_model=ShipmentRead, status_code=status.HTTP_201_CREATED)
def create_shipment(payload: ShipmentCreate, db: DbSession) -> ShipmentRead:
    if db.scalar(select(Shipment).where(Shipment.shipment_id == payload.shipment_id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Shipment {payload.shipment_id} already exists"
        )
    shipment = Shipment(**payload.model_dump())
    db.add(shipment)
    db.commit()
    db.refresh(shipment)
    return ShipmentRead.model_validate(shipment)
