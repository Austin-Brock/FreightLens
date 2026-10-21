# Architecture

## The whole system in one picture

```
  shipments.csv            contracted_rates.csv        carrier_invoices.csv
  (TMS extract)            (carrier agreements)        (carrier freight bills)
       │                            │                           │
       └────────────────────────────┼───────────────────────────┘
                                    ▼
                         services/ingestion.py
            normalize headers · parse money/dates · canonicalize
            charge codes · group invoice lines · match invoice→shipment
                                    ▼
                              SQLite (6 tables)
                                    ▼
                        services/audit_engine.py
           RateBook lookup → expected cost → 7 rules → AuditFinding
                                    ▼
                            audit_exceptions
                   (billed vs expected vs variance, per rule)
                                    ▼
                    FastAPI  ──────────────►  frontend/index.html
                   /api/v1                    dashboard + drill-down
```

Three inputs, one normalization step, one audit step, one output. That is the
entire concept, and every file in the repo serves one of those boxes.

## Why it's shaped this way

**The audit engine is pure.** `AuditEngine` takes invoices, shipments and a
`RateBook` and returns `AuditFinding` objects. It holds no database session and
commits nothing. `run_audit()` is a separate ~70-line function that persists the
result. This is the one design decision worth defending: it means every rule is
unit-testable without a database, and the rules can be re-run against history
without side effects. 29 of the 89 tests exercise rules with no database at all.

**Rate resolution is "most specific wins."** `RateBook.find()` filters contract
rows on carrier + mode + charge code, then matches service level, origin state
and destination state where `*` means "any," checks the weight break and the
effective-date window, and ranks survivors by how many non-wildcard fields they
match. A `CA→TX` row beats a national `*→*` row. This is what lets one small
table express a real tariff instead of one row per lane.

**Normalization is defensive and loud.** Real freight data arrives with
inconsistent headers, five date formats, money as `$1,250.50` or `(40.00)`, and
carrier-specific accessorial labels. `ingestion.py` normalizes all of it and
returns every unusable row as an `IngestionError` with its row number — a bad
row never fails the file and never disappears silently.

**Money is explicit.** Every amount is `Decimal`, rounded half-up to cents the
way carriers round — never float, never banker's rounding. A positive
`variance_amount` always means the carrier overbilled.

## The six tables

```
shipments ─────┬──< carrier_invoices ──< invoice_line_items
               │            │
               └──< audit_exceptions >── audit_runs
```

| Table | Answers |
|---|---|
| `shipments` | What did we ship? (lane, mode, weight, service, accessorials ordered) |
| `contracted_rates` | What should it cost? (rate type, weight breaks, discounts, minimums, effective dates) |
| `carrier_invoices` | What were we billed? (header, total, shipment reference) |
| `invoice_line_items` | Billed how, line by line? (charge code, quantity, amount) |
| `audit_runs` | When did we audit, and what did it total? |
| `audit_exceptions` | What was wrong? (rule, billed, expected, variance, recommendation) |

`carrier_invoices` keeps `shipment_ref` (whatever the carrier printed) separate
from `shipment_id` (the resolved foreign key). That separation is deliberate: an
invoice whose reference matches nothing is not a bug to be discarded, it's the
`MISSING_SHIPMENT` finding.

## The seven rules

Each rule is one comparison between two of the three data sources.

| Rule | Compares | Catches |
|---|---|---|
| `RATE_MISMATCH` | invoice ↔ contract | Wrong tariff: discount ignored, national rate used instead of a lane rate, wrong weight break |
| `FUEL_SURCHARGE_VARIANCE` | invoice ↔ contract | Fuel above the contracted percentage — priced off the *contracted* linehaul, so an inflated linehaul doesn't legitimise the fuel stacked on it |
| `UNCONTRACTED_CHARGE` | invoice ↔ contract | A charge with no rate on file at all |
| `ACCESSORIAL_NOT_ORDERED` | invoice ↔ shipment | A service billed that the shipper never requested |
| `DUPLICATE_INVOICE` | invoice ↔ other invoices | Same carrier, shipment and amount billed twice |
| `SERVICE_FAILURE` | shipment ↔ commitment | Delivery after the promised date. Claimable only on a guaranteed service, where the transportation charges are refundable; on any other service the miss is tracked at $0 |
| `MISSING_SHIPMENT` | invoice ↔ nothing | A bill that matches no shipment of record — a data-quality failure, flagged but never claimed as savings |

**Tolerances** (`app/config.py`): variances under **$1.00** or **2%** of
expected are treated as rounding noise. Both carriers and shippers round;
auditing at the penny produces findings nobody can action.

**Precedence matters.** `MISSING_SHIPMENT` short-circuits — with no shipment
there is nothing to validate, so no other rule runs. `ACCESSORIAL_NOT_ORDERED`
takes precedence over `UNCONTRACTED_CHARGE` on the same charge code, so one
accessorial never claims the same dollar twice. A claimable `SERVICE_FAILURE`
suppresses `RATE_MISMATCH` and `FUEL_SURCHARGE_VARIANCE` on the same invoice —
the guarantee refunds the whole transportation charge, and you do not argue the
rate on a bill you are refusing — while accessorials still stand, because those
services were performed. `MISSING_SHIPMENT` is marked non-monetary and is
excluded from recoverable totals.

## Request flow

```
GET  /                              → frontend/index.html
GET  /api/v1/analytics/summary      → KPI tiles + per-rule breakdown  (1 call)
GET  /api/v1/analytics/carriers     → the carrier performance table
GET  /api/v1/audits/exceptions      → the exceptions table
POST /api/v1/audits/run             → re-audit, then the page reloads
GET  /api/v1/invoices/{id}/audit    → the drill-down: shipment + invoice + findings
GET  /api/v1/audits/exceptions.csv  → the export link in the header
```

`/analytics/carriers` and `/analytics/lanes` each run a few narrow aggregates
and merge them in Python rather than one wide join, because the invoice window
keys off `invoice_date` while the shipment window keys off `ship_date` — a
join across both either drops carriers or double-counts their spend.

## Schema migrations

`backend/migrations/` holds the Alembic chain; revision 0001 is the whole
schema. Local startup still uses `create_all` for speed and then stamps the
database at head, so `alembic upgrade head` is a no-op on a dev database
instead of failing against tables that already exist. A test runs
`compare_metadata` against the migrated schema, so a model change without a
revision fails CI.

## The dashboard

The dashboard is one static HTML file with vanilla JS, served by FastAPI itself.
No build step, no `node_modules`, no second port — `uvicorn app.main:app` and
open `localhost:8000`. For a four-tile dashboard that is the right trade.

## Deliberately not here

SQLite, not Postgres. No Docker, no queues, no auth, no background jobs.
The data model and the migration chain are written so swapping `DATABASE_URL`
to Postgres is the only change needed — but adding that infrastructure would
make the project harder to read without making the freight-audit concept any
clearer.
