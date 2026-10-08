"""Data ingestion endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile, status

from app.api.deps import DbSession
from app.config import settings
from app.schemas import IngestionResult
from app.services import ingestion

router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post("/sample-data", response_model=dict[str, IngestionResult])
def load_sample_data(db: DbSession) -> dict[str, IngestionResult]:
    """Load the bundled ``sample_data/`` fixtures. Idempotent (upserts by key)."""
    if not settings.sample_data_dir.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Sample data directory not found: {settings.sample_data_dir}",
        )
    return ingestion.ingest_directory(db, settings.sample_data_dir)


@router.post("/{dataset}", response_model=IngestionResult)
async def upload_dataset(dataset: str, file: UploadFile, db: DbSession) -> IngestionResult:
    """Upload a CSV for ``shipments``, ``carrier_invoices`` or ``contracted_rates``."""
    if dataset not in ingestion.DATASETS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown dataset {dataset!r}; expected one of {', '.join(ingestion.DATASETS)}",
        )
    payload = await file.read()
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
    result = ingestion.ingest_dataset(db, dataset, payload)
    # A late-arriving shipment file should adopt invoices that landed first.
    if dataset == "shipments":
        ingestion.relink_invoices(db)
    return result
