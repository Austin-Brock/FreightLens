"""Shared fixtures: an in-memory database, factories and an API client."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    CarrierInvoice,
    ContractedRate,
    InvoiceLineItem,
    RateType,
    Shipment,
    TransportMode,
)
from app.services import ingestion

SAMPLE_DATA = settings.sample_data_dir


@pytest.fixture
def db() -> Session:
    """A fresh in-memory database per test.

    ``StaticPool`` keeps every connection pointed at the same in-memory
    database, which SQLite otherwise discards when a connection closes.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db: Session) -> TestClient:
    """API client wired to the test database.

    ``TestClient`` is used without its context manager on purpose: that skips
    the app lifespan, so tests never touch the real database or seed files.
    """
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def seeded_db(db: Session) -> Session:
    """The bundled sample data, loaded."""
    ingestion.ingest_directory(db, SAMPLE_DATA)
    return db


@pytest.fixture
def seeded_client(client: TestClient, seeded_db: Session) -> TestClient:
    return client


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_shipment(db: Session | None = None, **overrides) -> Shipment:
    defaults = dict(
        shipment_id="SHP-1",
        carrier_scac="ODFL",
        mode=TransportMode.LTL,
        service_level="standard",
        origin_state="CA",
        origin_zip="90021",
        dest_state="TX",
        dest_zip="75201",
        ship_date=date(2026, 3, 2),
        promised_date=date(2026, 3, 6),
        delivery_date=date(2026, 3, 5),
        weight_lb=Decimal("1000"),
        pieces=2,
    )
    shipment = Shipment(**{**defaults, **overrides})
    if db is not None:
        db.add(shipment)
        db.flush()
    return shipment


def make_rate(db: Session | None = None, **overrides) -> ContractedRate:
    defaults = dict(
        carrier_scac="ODFL",
        mode=TransportMode.LTL,
        service_level="*",
        origin_state="*",
        dest_state="*",
        charge_code="FREIGHT",
        rate_type=RateType.PER_CWT,
        base_rate=Decimal("30.00"),
        min_weight_lb=Decimal("0"),
        max_weight_lb=None,
        discount_pct=Decimal("0"),
        fuel_surcharge_pct=Decimal("0"),
        effective_date=date(2026, 1, 1),
        expiration_date=date(2026, 12, 31),
    )
    rate = ContractedRate(**{**defaults, **overrides})
    if db is not None:
        db.add(rate)
        db.flush()
    return rate


def make_invoice(
    db: Session | None = None,
    *,
    shipment: Shipment | None = None,
    lines: list[tuple[str, Decimal | str]] | None = None,
    total: Decimal | str | None = None,
    **overrides,
) -> CarrierInvoice:
    """Build an invoice. ``lines`` is ``[(charge_code, amount), ...]``."""
    defaults = dict(
        invoice_number="INV-1",
        carrier_scac=(shipment.carrier_scac if shipment else "ODFL"),
        shipment_ref=(shipment.shipment_id if shipment else None),
        invoice_date=date(2026, 3, 10),
        currency="USD",
    )
    invoice = CarrierInvoice(**{**defaults, **overrides})
    for index, (charge_code, amount) in enumerate(lines or [], start=1):
        invoice.line_items.append(
            InvoiceLineItem(
                line_number=index, charge_code=charge_code, amount=Decimal(str(amount))
            )
        )
    invoice.total_amount = (
        Decimal(str(total)) if total is not None else invoice.line_total
    )
    if shipment is not None:
        invoice.shipment = shipment
    if db is not None:
        db.add(invoice)
        db.flush()
    return invoice


@pytest.fixture
def factories():
    return {"shipment": make_shipment, "rate": make_rate, "invoice": make_invoice}


@pytest.fixture
def sample_data_dir() -> Path:
    return SAMPLE_DATA
