# FreightLens

Freight invoice auditing and spend intelligence. Ingests shipment records,
carrier agreements and carrier invoices, normalizes them, and audits every
invoice against the contract to find billing discrepancies.

```
  Shipment Data ─────┐
                     │
  Contract Rates ────┼──→  Normalize  →  Audit  →  Exceptions  →  Savings
                     │
  Carrier Invoice ───┘
```

## Run it

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open **http://localhost:8000** for the dashboard, or
**http://localhost:8000/docs** for the API.

On first start it creates the SQLite database, loads `sample_data/`, and runs
the audit — so the dashboard is populated immediately. Delete
`backend/freightlens.db` to start over.

```bash
pytest            # 89 tests, ~1s
```

## What it finds in the sample data

24 shipments, 32 contracted rates, 27 carrier invoices across six carriers and
three modes (parcel, LTL, truckload):

| | |
|---|---|
| Freight spend | **$20,639.85** |
| Invoices audited | **27** |
| Exceptions detected | **12** |
| Recoverable | **$1,329.15** (6.4% of spend) |

| Rule | Count | Variance |
|---|---|---|
| Linehaul billed above contracted rate | 2 | $508.00 |
| Accessorial billed but never ordered | 4 | $335.45 |
| Duplicate invoice for the same shipment | 1 | $217.62 |
| Fuel surcharge above contracted percentage | 3 | $158.88 |
| Charge has no contracted rate | 1 | $109.20 |
| Invoice cannot be matched to a shipment | 1 | — |

The largest finding: XPO billed **$900.00** of linehaul on a CA→TX lane
contracted at **$480.00** ($32.00/cwt less a 40% lane discount on 2,500 lb) —
they rated the undiscounted national tariff — and then stacked 24% fuel on the
inflated linehaul, for **$520.80** total on one invoice.

## Layout

```
backend/app/
  models/      6 SQLAlchemy tables
  schemas/     Pydantic request/response models
  api/         FastAPI routers (21 endpoints under /api/v1)
  services/
    ingestion.py     CSV → normalized records
    audit_engine.py  pure rule engine + thin persistence wrapper
    rules.py         the 6-rule catalogue
    analytics.py     the dashboard summary
  db/          engine, session, schema creation, sample-data seed
backend/tests/ 89 tests
frontend/      index.html — the dashboard, one file, no build step
sample_data/   the three input CSVs
docs/          architecture.md · glossary.md · implementation-plan.md
```

## Docs

- **[docs/architecture.md](docs/architecture.md)** — data flow, schema, the six
  rules and why the engine is split the way it is
- **[docs/glossary.md](docs/glossary.md)** — freight terminology (CWT, weight
  breaks, accessorials, FSC, LTL vs. TL) tied to where each concept lives in
  the schema
- **[docs/implementation-plan.md](docs/implementation-plan.md)** — the
  customer-implementation side: a 10-week plan, RAID log, and a worked change
  request for adding a seventh carrier mid-project

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · SQLite · pytest.
No Docker, no message queue, no build toolchain — deliberately.
