"""Analytics response models."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import ExceptionSeverity, TransportMode


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


class CarrierScorecard(BaseModel):
    """How one carrier is performing on price, accuracy and service."""

    carrier_scac: str
    carrier_name: str | None = None

    shipments: int
    invoices: int
    total_billed: Decimal
    exceptions: int
    total_variance: Decimal = Field(description="Overcharge found across monetary rules.")
    recoverable_amount: Decimal = Field(description="Open and disputed overcharges.")

    variance_rate: Decimal = Field(
        description="Variance / billed — the carrier's error rate in dollars."
    )
    on_time_rate: Decimal | None = Field(
        default=None,
        description="Deliveries that met the promised date, over those with both dates on file.",
    )


class LaneBreakdown(BaseModel):
    """What one origin/destination pair costs, and how clean its billing is.

    Lanes are the unit freight is negotiated in, so this is the view that feeds
    a bid: cost per hundredweight is what a carrier's rate sheet is quoted in.
    """

    origin_state: str
    dest_state: str
    mode: TransportMode

    shipments: int
    invoices: int
    total_weight_lb: Decimal
    total_billed: Decimal
    exceptions: int
    total_variance: Decimal

    avg_cost_per_shipment: Decimal
    cost_per_cwt: Decimal | None = Field(
        default=None, description="Billed dollars per 100 lb — how carriers quote LTL."
    )

    @property
    def lane(self) -> str:
        return f"{self.origin_state}->{self.dest_state}"
