"""The migration chain has to produce exactly what the models describe.

``init_db`` uses ``create_all`` for local convenience, which means a model
change can land without a matching revision and nobody notices until a
production deploy. These tests run the real chain against a scratch database
and diff the result against the ORM metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "alembic_version",
    "audit_exceptions",
    "audit_runs",
    "carrier_invoices",
    "contracted_rates",
    "invoice_line_items",
    "shipments",
}


@pytest.fixture
def alembic_config(tmp_path: Path) -> tuple[Config, str]:
    url = f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def test_upgrade_head_creates_every_table(alembic_config):
    config, url = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_migrations_match_the_models(alembic_config):
    """No autogenerate diff — the chain and the ORM agree."""
    config, url = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], f"models have drifted from the migrations: {diff}"


def test_downgrade_leaves_a_clean_database(alembic_config):
    config, url = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url)
    try:
        # Only alembic's own bookkeeping table survives a full downgrade.
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
