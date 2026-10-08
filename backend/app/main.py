"""FreightLens API entrypoint.

Run locally with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import api_router
from app.config import settings
from app.db.init_db import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DESCRIPTION = """
Freight invoice auditing and spend intelligence.

* **Ingest** shipments, carrier invoices and contracted rates from CSV.
* **Audit** every invoice against the contract — rate mismatches, duplicate
  billing, uncontracted accessorials, fuel variance, service failures.
* **Analyse** spend by carrier, mode and lane, and track recovery.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    init_db(seed=True)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """The single-page dashboard. No build step — one HTML file, vanilla JS."""
    return FileResponse(settings.frontend_dir / "index.html")
