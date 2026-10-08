"""HTTP API. ``api_router`` is the single router ``main`` mounts."""

from fastapi import APIRouter

from app.api import analytics, audits, ingestion, invoices, rates, shipments

api_router = APIRouter()
api_router.include_router(shipments.router)
api_router.include_router(invoices.router)
api_router.include_router(rates.router)
api_router.include_router(audits.router)
api_router.include_router(analytics.router)
api_router.include_router(ingestion.router)

__all__ = ["api_router"]
