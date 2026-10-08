"""Schema creation and optional sample-data seeding."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import AuditRun, ContractedRate, Shipment  # noqa: F401  (registers mappers)
from app.services import ingestion
from app.services.audit_engine import run_audit

logger = logging.getLogger(__name__)


def create_schema() -> None:
    """Create any missing tables. Alembic owns migrations in production."""
    Base.metadata.create_all(bind=engine)


def seed_sample_data(db: Session | None = None, *, force: bool = False) -> dict[str, dict]:
    """Load ``sample_data/*.csv`` so a fresh checkout has something to audit.

    Idempotent: ingestion upserts on business keys, and unless ``force`` is set
    the seed is skipped entirely once shipments exist.
    """
    owns_session = db is None
    db = db or SessionLocal()
    try:
        if not force and db.query(Shipment).first() is not None:
            logger.info("Sample data already present; skipping seed.")
            return {}
        return ingestion.ingest_directory(db, settings.sample_data_dir)
    finally:
        if owns_session:
            db.close()


def init_db(*, seed: bool = True) -> None:
    """Prepare the database so a fresh clone opens on a populated dashboard."""
    create_schema()
    if not seed:
        return
    db = SessionLocal()
    try:
        seed_sample_data(db)
        if db.query(AuditRun).first() is None:
            run = run_audit(db)
            logger.info(
                "Startup audit: %d exception(s), %s recoverable",
                run.exceptions_found,
                run.total_variance,
            )
    finally:
        db.close()
