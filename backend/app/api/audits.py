"""Audit run and exception endpoints."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import DbSession, PageParams
from app.models import AuditException, AuditRun, CarrierInvoice
from app.schemas import (
    AuditExceptionRead,
    AuditExceptionUpdate,
    AuditRunRead,
    AuditRunRequest,
    AuditRunResult,
    Page,
)
from app.services.audit_engine import run_audit
from app.services.rules import RULES

router = APIRouter(prefix="/audits", tags=["audits"])


@router.get("/rules")
def list_rules() -> list[dict]:
    """The rule catalogue, for UI legends and documentation."""
    return [
        {
            "code": spec.code,
            "name": spec.name,
            "severity": spec.severity,
            "description": spec.description,
            "monetary": spec.monetary,
        }
        for spec in RULES.values()
    ]


@router.post("/run", response_model=AuditRunResult, status_code=status.HTTP_201_CREATED)
def trigger_audit(db: DbSession, payload: AuditRunRequest | None = None) -> AuditRunResult:
    run = run_audit(db, payload or AuditRunRequest())
    exceptions = db.scalars(
        select(AuditException)
        .where(AuditException.run_id == run.id)
        .order_by(AuditException.variance_amount.desc())
    ).all()
    return AuditRunResult(
        run=AuditRunRead.model_validate(run),
        exceptions=[AuditExceptionRead.model_validate(exc) for exc in exceptions],
    )


@router.get("/runs", response_model=list[AuditRunRead])
def list_runs(db: DbSession, limit: int = Query(20, ge=1, le=100)) -> list[AuditRunRead]:
    runs = db.scalars(select(AuditRun).order_by(AuditRun.started_at.desc()).limit(limit)).all()
    return [AuditRunRead.model_validate(run) for run in runs]


@router.get("/runs/latest", response_model=AuditRunRead)
def latest_run(db: DbSession) -> AuditRunRead:
    run = db.scalar(select(AuditRun).order_by(AuditRun.started_at.desc()).limit(1))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No audit has been run yet")
    return AuditRunRead.model_validate(run)


def _filtered_exceptions(
    *,
    rule_code: str | None = None,
    severity: str | None = None,
    exception_status: str | None = None,
    carrier_scac: str | None = None,
    run_id: int | None = None,
    min_variance: float | None = None,
) -> Select:
    """The one place exception filters are interpreted, for JSON and CSV alike."""
    stmt = select(AuditException)
    if rule_code:
        stmt = stmt.where(AuditException.rule_code == rule_code.upper())
    if severity:
        stmt = stmt.where(AuditException.severity == severity.lower())
    if exception_status:
        stmt = stmt.where(AuditException.status == exception_status.lower())
    if run_id:
        stmt = stmt.where(AuditException.run_id == run_id)
    if min_variance is not None:
        stmt = stmt.where(AuditException.variance_amount >= min_variance)
    if carrier_scac:
        stmt = stmt.join(
            CarrierInvoice, AuditException.invoice_id == CarrierInvoice.id
        ).where(CarrierInvoice.carrier_scac == carrier_scac.upper())
    return stmt


@router.get("/exceptions", response_model=Page[AuditExceptionRead])
def list_exceptions(
    db: DbSession,
    page: PageParams,
    rule_code: str | None = None,
    severity: str | None = None,
    exception_status: str | None = Query(None, alias="status"),
    carrier_scac: str | None = None,
    run_id: int | None = None,
    min_variance: float | None = None,
) -> Page[AuditExceptionRead]:
    stmt = _filtered_exceptions(
        rule_code=rule_code,
        severity=severity,
        exception_status=exception_status,
        carrier_scac=carrier_scac,
        run_id=run_id,
        min_variance=min_variance,
    )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditException.variance_amount.desc(), AuditException.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[AuditExceptionRead](
        items=[AuditExceptionRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


DISPUTE_COLUMNS = (
    "carrier_scac",
    "invoice_number",
    "invoice_date",
    "shipment_reference",
    "rule_code",
    "rule_name",
    "severity",
    "status",
    "charge_code",
    "currency",
    "billed_amount",
    "expected_amount",
    "variance_amount",
    "finding",
    "requested_action",
)


def _dispute_rows(db: Session, stmt: Select) -> Iterator[str]:
    """Stream the packet so a large recovery claim never buffers in memory."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        buffer.seek(0)
        chunk = buffer.read()
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    writer.writerow(DISPUTE_COLUMNS)
    yield flush()

    rows = db.scalars(
        stmt.options(selectinload(AuditException.invoice))
        .order_by(AuditException.variance_amount.desc(), AuditException.id)
        .execution_options(yield_per=200)
    )
    for exception in rows:
        invoice = exception.invoice
        writer.writerow(
            [
                exception.carrier_scac or "",
                exception.invoice_number or "",
                invoice.invoice_date.isoformat() if invoice else "",
                exception.shipment_reference or "",
                exception.rule_code,
                exception.rule_name,
                exception.severity,
                exception.status,
                exception.charge_code,
                exception.currency,
                f"{exception.billed_amount:.2f}",
                f"{exception.expected_amount:.2f}",
                f"{exception.variance_amount:.2f}",
                exception.message,
                exception.recommendation or "",
            ]
        )
        yield flush()


@router.get(
    "/exceptions.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "One row per finding."}},
)
def export_exceptions(
    db: DbSession,
    rule_code: str | None = None,
    severity: str | None = None,
    exception_status: str | None = Query(None, alias="status"),
    carrier_scac: str | None = None,
    run_id: int | None = None,
    min_variance: float | None = None,
) -> StreamingResponse:
    """The dispute packet: every finding as a row, worst dollars first.

    A recovery claim goes to the carrier as a spreadsheet, so the same filters
    the exception list takes produce the file — ``?carrier_scac=XPOL`` is one
    carrier's claim, ``?status=open`` is everything still outstanding.
    """
    stmt = _filtered_exceptions(
        rule_code=rule_code,
        severity=severity,
        exception_status=exception_status,
        carrier_scac=carrier_scac,
        run_id=run_id,
        min_variance=min_variance,
    )
    scope = (carrier_scac or "all-carriers").lower()
    filename = f"freightlens-disputes-{scope}-{date.today().isoformat()}.csv"
    return StreamingResponse(
        _dispute_rows(db, stmt),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/exceptions/{exception_id}", response_model=AuditExceptionRead)
def update_exception(
    exception_id: int, payload: AuditExceptionUpdate, db: DbSession
) -> AuditExceptionRead:
    """Work an exception: dispute it, mark it recovered, or dismiss it."""
    exception = db.get(AuditException, exception_id)
    if exception is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Exception {exception_id} not found")
    exception.status = payload.status
    if payload.resolution_note is not None:
        exception.resolution_note = payload.resolution_note
    db.commit()
    db.refresh(exception)
    return AuditExceptionRead.model_validate(exception)
