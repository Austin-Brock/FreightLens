"""Contracted-rate endpoints."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import DbSession, PageParams
from app.models import ContractedRate
from app.schemas import ContractedRateCreate, ContractedRateRead, Page
from app.services.audit_engine import RateBook, UnpriceableCharge, expected_charge

router = APIRouter(prefix="/rates", tags=["rates"])


@router.get("", response_model=Page[ContractedRateRead])
def list_rates(
    db: DbSession,
    page: PageParams,
    carrier_scac: str | None = None,
    mode: str | None = None,
    charge_code: str | None = None,
) -> Page[ContractedRateRead]:
    stmt = select(ContractedRate)
    if carrier_scac:
        stmt = stmt.where(ContractedRate.carrier_scac == carrier_scac.upper())
    if mode:
        stmt = stmt.where(ContractedRate.mode == mode.lower())
    if charge_code:
        stmt = stmt.where(ContractedRate.charge_code == charge_code.upper())

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(
            ContractedRate.carrier_scac,
            ContractedRate.charge_code,
            ContractedRate.min_weight_lb,
        )
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[ContractedRateRead](
        items=[ContractedRateRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=ContractedRateRead, status_code=status.HTTP_201_CREATED)
def create_rate(payload: ContractedRateCreate, db: DbSession) -> ContractedRateRead:
    rate_row = ContractedRate(**payload.model_dump())
    db.add(rate_row)
    db.commit()
    db.refresh(rate_row)
    return ContractedRateRead.model_validate(rate_row)


@router.get("/quote", tags=["rates"])
def quote(
    db: DbSession,
    carrier_scac: str,
    mode: str,
    origin_state: str,
    dest_state: str,
    weight_lb: Decimal = Decimal("0"),
    distance_mi: Decimal | None = None,
    service_level: str = "*",
    charge_code: str = "FREIGHT",
    ship_date: date | None = None,
) -> dict:
    """What *should* this move cost under contract? Useful for pre-audit checks."""
    rate_row = RateBook.from_db(db, carrier_scac).find(
        carrier_scac=carrier_scac,
        mode=mode.lower(),
        charge_code=charge_code,
        service_level=service_level,
        origin_state=origin_state,
        dest_state=dest_state,
        weight_lb=weight_lb,
        when=ship_date,
    )
    if rate_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No effective {charge_code} rate for {carrier_scac.upper()} "
            f"{origin_state.upper()}->{dest_state.upper()} at {weight_lb} lb",
        )
    try:
        expected_amount = expected_charge(
            rate_row, weight_lb=weight_lb, distance_mi=distance_mi
        )
    except UnpriceableCharge as exc:
        # The rate exists, the request just cannot drive it — a 422, not a 404.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        "rate": ContractedRateRead.model_validate(rate_row),
        "expected_amount": expected_amount,
    }
