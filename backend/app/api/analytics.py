"""Spend intelligence endpoint."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.api.deps import DbSession
from app.schemas import CarrierScorecard, KpiSummary
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=KpiSummary)
def summary(db: DbSession, start: date | None = None, end: date | None = None) -> KpiSummary:
    """Freight spend, exceptions, recoverable dollars and a per-rule breakdown."""
    return analytics.kpi_summary(db, start=start, end=end)


@router.get("/carriers", response_model=list[CarrierScorecard])
def carriers(
    db: DbSession, start: date | None = None, end: date | None = None
) -> list[CarrierScorecard]:
    """Per-carrier spend, billing accuracy and on-time rate, worst dollars first."""
    return analytics.carrier_scorecards(db, start=start, end=end)
