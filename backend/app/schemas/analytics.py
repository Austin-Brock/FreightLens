"""Analytics response models — one summary, enough for the dashboard."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import ExceptionSeverity


class RuleBreakdown(BaseModel):
    """Exceptions and dollars attributable to one audit rule."""

    rule_code: str
    rule_name: str
    severity: ExceptionSeverity
    count: int
    variance_amount: Decimal


class KpiSummary(BaseModel):
    """Everything the executive dashboard needs, in one response."""

    shipments: int
    invoices_audited: int
    exceptions_detected: int

    total_billed: Decimal = Field(description="Total freight spend across invoices in scope.")
    total_expected: Decimal = Field(description="What the contract says that spend should be.")
    total_variance: Decimal = Field(description="Billed minus expected, across all rules.")

    recoverable_amount: Decimal = Field(description="Open and disputed overcharges.")
    recovered_amount: Decimal = Field(description="Overcharges credited back by the carrier.")

    exception_rate: Decimal = Field(description="Invoices with >=1 exception / invoices audited.")
    overcharge_rate: Decimal = Field(description="Variance / total billed.")
    average_cost_per_shipment: Decimal

    by_rule: list[RuleBreakdown] = Field(
        default_factory=list, description="Variance by audit rule, largest first."
    )
