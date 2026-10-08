"""Audit request/response models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import AuditRunStatus, ExceptionSeverity, ExceptionStatus
from app.schemas.common import ORMModel


class AuditRunRequest(ORMModel):
    """Scope selector for an audit run. Omit everything to audit all invoices."""

    carrier_scac: str | None = None
    invoice_ids: list[int] | None = None
    invoiced_from: date | None = None
    invoiced_to: date | None = None
    triggered_by: str = "api"
    replace_existing: bool = Field(
        default=True,
        description="Clear prior exceptions for the invoices in scope so re-runs do not duplicate.",
    )


class AuditExceptionRead(ORMModel):
    id: int
    run_id: int | None = None
    invoice_id: int | None = None
    shipment_id: int | None = None
    invoice_number: str | None = None
    carrier_scac: str | None = None
    shipment_reference: str | None = None
    rule_code: str
    rule_name: str
    severity: ExceptionSeverity
    status: ExceptionStatus
    charge_code: str
    billed_amount: Decimal
    expected_amount: Decimal
    variance_amount: Decimal
    currency: str
    message: str
    recommendation: str | None = None
    resolution_note: str | None = None
    created_at: datetime


class AuditExceptionUpdate(ORMModel):
    status: ExceptionStatus
    resolution_note: str | None = None


class AuditRunRead(ORMModel):
    id: int
    started_at: datetime
    completed_at: datetime | None = None
    status: AuditRunStatus
    triggered_by: str
    invoices_audited: int
    exceptions_found: int
    total_billed: Decimal
    total_variance: Decimal
    notes: str | None = None
    duration_seconds: float | None = None


class AuditRunResult(ORMModel):
    """What the POST /audits/run endpoint returns."""

    run: AuditRunRead
    exceptions: list[AuditExceptionRead] = Field(default_factory=list)
