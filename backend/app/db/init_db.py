"""Schema creation and optional sample-data seeding."""

from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.orm import Session

from app.config import BACKEND_ROOT, settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import AuditRun, ContractedRate, Shipment  # noqa: F401  (registers mappers)
from app.services import ingestion
from app.services.audit_engine import run_audit

logger = logging.getLogger(__name__)


def create_schema() -> None:
    """Create any missing tables. Alembic owns migrations in production."""
    Base.metadata.create_all(bind=engine)
    _stamp_head()


def _stamp_head() -> None:
    """Record a create_all database as being at head.

    Without this, a local database built by ``create_all`` has no
    ``alembic_version`` row, so the next ``alembic upgrade head`` replays
    revision 0001 against tables that already exist and fails. Stamping only
    happens when the database has no revision recorded at all, so a real
    migrated database is never touched.
    """
    alembic_ini = BACKEND_ROOT / "alembic.ini"
    if not alembic_ini.exists():  # pragma: no cover - only in a trimmed deploy
        return
    with engine.begin() as connection:
        if MigrationContext.configure(connection).get_current_revision() is not None:
            return
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.stamp(config, "head")
    logger.info("Stamped the schema at head.")


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
