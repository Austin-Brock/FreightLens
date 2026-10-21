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
make install      # venv + dependencies
make dev          # http://localhost:8000
```

Open **http://localhost:8000** for the dashboard, or
**http://localhost:8000/docs** for the API.

On first start it creates the SQLite database, loads `sample_data/`, and runs
the audit — so the dashboard is populated immediately.

```bash
make test         # 108 tests, ~2s
make lint         # ruff
make audit        # re-audit every invoice, print the totals
make reset        # drop the local database; the next start re-seeds it
make migrate      # apply migrations (make revision m="..." to add one)
```

`make help` lists everything. The long form still works if you prefer it:
`cd backend && python3 -m venv .venv && pip install -r requirements-dev.txt`,
then `uvicorn app.main:app --reload`.

## What it finds in the sample data

24 shipments, 32 contracted rates, 27 carrier invoices across six carriers and
three modes (parcel, LTL, truckload):

| | |
|---|---|
| Freight spend | **$20,639.85** |
| Invoices audited | **27** |
| Exceptions detected | **15** |
| Recoverable | **$1,691.49** (8.2% of spend) |

| Rule | Count | Variance |
|---|---|---|
| Linehaul billed above contracted rate | 2 | $508.00 |
| Delivered after the service commitment | 3 | $362.34 |
| Accessorial billed but never ordered | 4 | $335.45 |
| Duplicate invoice for the same shipment | 1 | $217.62 |
| Fuel surcharge above contracted percentage | 3 | $158.88 |
| Charge has no contracted rate | 1 | $109.20 |
| Invoice cannot be matched to a shipment | 1 | — |

Only one of the three late deliveries carries a claim: ODFL missed a
**guaranteed** LTL commitment by two days, which refunds the $362.34 of
freight and fuel. The other two ran late on standard and express service,
where the tariff promises a transit estimate and not a refund — they are
tracked against the carrier scorecard at $0.

The largest finding: XPO billed **$900.00** of linehaul on a CA→TX lane
contracted at **$480.00** ($32.00/cwt less a 40% lane discount on 2,500 lb) —
they rated the undiscounted national tariff — and then stacked 24% fuel on the
inflated linehaul, for **$520.80** total on one invoice.

## Layout

```
backend/app/
  models/      6 SQLAlchemy tables
  schemas/     Pydantic request/response models
  api/         FastAPI routers (24 endpoints under /api/v1)
  services/
    ingestion.py     CSV → normalized records
    audit_engine.py  pure rule engine + thin persistence wrapper
    rules.py         the 7-rule catalogue
    analytics.py     KPI summary, carrier scorecards, lane breakdown
  db/          engine, session, schema creation, sample-data seed
backend/migrations/  Alembic chain; revision 0001 is the whole schema
backend/tests/ 108 tests
frontend/      index.html — the dashboard, one file, no build step
sample_data/   the three input CSVs
docs/          architecture.md · glossary.md · implementation-plan.md
```

## Docs

- **[docs/architecture.md](docs/architecture.md)** — data flow, schema, the
  seven rules and why the engine is split the way it is
- **[docs/glossary.md](docs/glossary.md)** — freight terminology (CWT, weight
  breaks, accessorials, FSC, LTL vs. TL) tied to where each concept lives in
  the schema
- **[docs/implementation-plan.md](docs/implementation-plan.md)** — the
  customer-implementation side: a 10-week plan, RAID log, and a worked change
  request for adding a seventh carrier mid-project

## The API

```
GET  /api/v1/analytics/summary       KPI tiles + per-rule breakdown
GET  /api/v1/analytics/carriers      spend, error rate and on-time rate per carrier
GET  /api/v1/analytics/lanes         spend and cost per cwt per lane
POST /api/v1/audits/run              re-audit, optionally scoped to a carrier or date range
GET  /api/v1/audits/exceptions       the findings, filterable and paged
GET  /api/v1/audits/exceptions.csv   the same findings as a dispute packet
GET  /api/v1/invoices/{id}/audit     drill-down: shipment + invoice + findings
GET  /api/v1/rates/quote             what should this move cost under contract?
POST /api/v1/ingest/{dataset}        upload a shipments / rates / invoices CSV
```

Full surface at **/docs**.

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · SQLite ·
pytest · ruff.
No Docker, no message queue, no build toolchain — deliberately.
