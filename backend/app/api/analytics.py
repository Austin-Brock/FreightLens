"""Spend intelligence endpoint."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.api.deps import DbSession
from app.schemas import KpiSummary
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=KpiSummary)
def summary(db: DbSession, start: date | None = None, end: date | None = None) -> KpiSummary:
    """Freight spend, exceptions, recoverable dollars and a per-rule breakdown."""
    return analytics.kpi_summary(db, start=start, end=end)
