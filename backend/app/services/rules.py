"""Catalogue of audit rules.

Keeping the metadata in one table means the API, the UI legend and the docs all
describe a rule the same way, and adding a rule is a two-step change: register
it here, implement it in :mod:`app.services.audit_engine`.

Seven rules, covering the four comparisons a freight audit actually makes:

* invoice vs. contract      -> RATE_MISMATCH, FUEL_SURCHARGE_VARIANCE, UNCONTRACTED_CHARGE
* invoice vs. shipment      -> ACCESSORIAL_NOT_ORDERED
* invoice vs. other invoices-> DUPLICATE_INVOICE
* invoice vs. nothing       -> MISSING_SHIPMENT (a data-quality failure)

and one that compares the shipment against what the service promised:

* shipment vs. commitment   -> SERVICE_FAILURE
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ExceptionSeverity


@dataclass(frozen=True)
class RuleSpec:
    code: str
    name: str
    severity: ExceptionSeverity
    description: str
    # Informational rules explain a problem but never claim money of their own,
    # so their variance is excluded from recovery totals.
    monetary: bool = True


RULES: dict[str, RuleSpec] = {
    spec.code: spec
    for spec in (
        RuleSpec(
            "RATE_MISMATCH",
            "Linehaul billed above contracted rate",
            ExceptionSeverity.HIGH,
            "Billed freight exceeds the contracted rate after discount and "
            "minimum charge are applied.",
        ),
        RuleSpec(
            "DUPLICATE_INVOICE",
            "Duplicate invoice for the same shipment",
            ExceptionSeverity.CRITICAL,
            "A second invoice bills the same carrier, shipment and amount as an "
            "earlier one.",
        ),
        RuleSpec(
            "ACCESSORIAL_NOT_ORDERED",
            "Accessorial billed but never ordered",
            ExceptionSeverity.HIGH,
            "The shipper did not request this service on the shipment of record.",
        ),
        RuleSpec(
            "UNCONTRACTED_CHARGE",
            "Charge has no contracted rate",
            ExceptionSeverity.HIGH,
            "An accessorial or surcharge appears on the invoice with no "
            "corresponding row in the carrier agreement.",
        ),
        RuleSpec(
            "FUEL_SURCHARGE_VARIANCE",
            "Fuel surcharge above contracted percentage",
            ExceptionSeverity.MEDIUM,
            "Fuel was assessed at a higher percentage of linehaul than the "
            "contract allows, or applied to an inflated linehaul.",
        ),
        RuleSpec(
            "SERVICE_FAILURE",
            "Delivered after the service commitment",
            ExceptionSeverity.HIGH,
            "Delivery missed the promised date. Under a guaranteed service the "
            "transportation charges are refundable; on any other service the "
            "miss is tracked but not claimable.",
        ),
        RuleSpec(
            "MISSING_SHIPMENT",
            "Invoice cannot be matched to a shipment",
            ExceptionSeverity.HIGH,
            "The carrier reference on the invoice matches no shipment of record, "
            "so the charge cannot be validated at all.",
            monetary=False,
        ),
    )
}


def rule(code: str) -> RuleSpec:
    return RULES[code]


MONETARY_RULES: frozenset[str] = frozenset(
    spec.code for spec in RULES.values() if spec.monetary
)

#: Service levels whose contract carries a money-back guarantee. Anything else
#: delivering late is a performance problem, not a billing one — a standard LTL
#: tariff promises a transit estimate, not a refund.
GUARANTEED_SERVICE_LEVELS: frozenset[str] = frozenset({"guaranteed", "guaranteed_am", "absolute"})


def is_guaranteed(service_level: str | None) -> bool:
    return (service_level or "").strip().lower() in GUARANTEED_SERVICE_LEVELS
