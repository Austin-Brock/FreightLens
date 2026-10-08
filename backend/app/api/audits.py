"""Audit run and exception endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DbSession, PageParams
from app.models import AuditException, AuditRun
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
        from app.models import CarrierInvoice  # local import avoids an unused global

        stmt = stmt.join(
            CarrierInvoice, AuditException.invoice_id == CarrierInvoice.id
        ).where(CarrierInvoice.carrier_scac == carrier_scac.upper())

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
