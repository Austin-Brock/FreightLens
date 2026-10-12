"""Freight invoice audit engine.

The engine is deliberately split in two:

* :class:`AuditEngine` is pure — it takes invoices, shipments and a
  :class:`RateBook` and returns :class:`AuditFinding` objects. No session, no
  commit, which is what makes the rules cheap to unit-test.
* :func:`run_audit` is the thin persistence wrapper the API calls.

Money convention: a **positive** ``variance_amount`` means the carrier billed
more than the contract allows, i.e. dollars the shipper can recover.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db.base import utcnow
from app.models import (
    ACCESSORIAL_CODES,
    WILDCARD,
    AuditException,
    AuditRun,
    AuditRunStatus,
    CarrierInvoice,
    ChargeCode,
    ContractedRate,
    ExceptionSeverity,
    InvoiceStatus,
    RateType,
    Shipment,
    TransportMode,
)
from app.schemas.audit import AuditRunRequest
from app.services.rules import RULES, RuleSpec, rule

CENTS = Decimal("0.01")
ZERO = Decimal("0")


def money(value: Decimal | int | float | str) -> Decimal:
    """Round to cents the way carriers do — half up, not banker's rounding."""
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass
class AuditFinding:
    """One discrepancy, not yet persisted."""

    rule_code: str
    charge_code: str
    message: str
    billed_amount: Decimal = ZERO
    expected_amount: Decimal = ZERO
    recommendation: str | None = None
    severity: ExceptionSeverity | None = None
    currency: str = "USD"

    @property
    def spec(self) -> RuleSpec:
        return rule(self.rule_code)

    @property
    def rule_name(self) -> str:
        return self.spec.name

    @property
    def variance_amount(self) -> Decimal:
        """Claimable dollars. Informational rules never claim."""
        if not self.spec.monetary:
            return ZERO
        return money(self.billed_amount - self.expected_amount)

    @property
    def resolved_severity(self) -> ExceptionSeverity:
        return self.severity or self.spec.severity


# ---------------------------------------------------------------------------
# Rate book
# ---------------------------------------------------------------------------


class RateBook:
    """Indexed view over contracted rates with most-specific-wins lookup."""

    def __init__(self, rates: Iterable[ContractedRate]) -> None:
        self._by_key: dict[tuple[str, str, str], list[ContractedRate]] = {}
        for rate_row in rates:
            key = (rate_row.carrier_scac.upper(), str(rate_row.mode), rate_row.charge_code.upper())
            self._by_key.setdefault(key, []).append(rate_row)

    @classmethod
    def from_db(cls, db: Session, carrier_scac: str | None = None) -> RateBook:
        stmt = select(ContractedRate)
        if carrier_scac:
            stmt = stmt.where(ContractedRate.carrier_scac == carrier_scac.upper())
        return cls(db.scalars(stmt).all())

    def has_carrier(self, carrier_scac: str) -> bool:
        prefix = carrier_scac.upper()
        return any(key[0] == prefix for key in self._by_key)

    def find(
        self,
        *,
        carrier_scac: str,
        mode: TransportMode | str,
        charge_code: str,
        service_level: str = WILDCARD,
        origin_state: str = WILDCARD,
        dest_state: str = WILDCARD,
        weight_lb: Decimal = ZERO,
        when: date | None = None,
    ) -> ContractedRate | None:
        """Return the most specific effective rate, or ``None``."""
        candidates = self._by_key.get(
            (carrier_scac.upper(), str(mode), charge_code.upper()), []
        )
        matches = [
            row
            for row in candidates
            if _matches(row.service_level, service_level)
            and _matches(row.origin_state, origin_state)
            and _matches(row.dest_state, dest_state)
            and row.covers_weight(weight_lb)
            and (when is None or row.covers_date(when))
        ]
        if not matches:
            return None
        # Most specific first; among equals, the newest effective date wins.
        matches.sort(key=lambda row: (row.specificity, row.effective_date), reverse=True)
        return matches[0]


def _matches(contract_value: str | None, shipment_value: str | None) -> bool:
    if not contract_value or contract_value == WILDCARD:
        return True
    if not shipment_value or shipment_value == WILDCARD:
        return True
    return contract_value.upper() == shipment_value.upper()


def expected_charge(
    rate_row: ContractedRate,
    *,
    weight_lb: Decimal = ZERO,
    distance_mi: Decimal | None = None,
    basis: Decimal = ZERO,
) -> Decimal:
    """Price one charge from a contract row.

    ``basis`` is the linehaul a percentage-type charge (fuel) applies to.
    """
    base = Decimal(rate_row.base_rate)
    if rate_row.rate_type == RateType.PER_CWT:
        gross = base * (Decimal(weight_lb) / Decimal("100"))
    elif rate_row.rate_type == RateType.PER_LB:
        gross = base * Decimal(weight_lb)
    elif rate_row.rate_type == RateType.PER_MILE:
        gross = base * Decimal(distance_mi or 0)
    elif rate_row.rate_type == RateType.PCT_OF_LINEHAUL:
        gross = base * Decimal(basis)
    else:  # FLAT — a fixed charge per shipment
        gross = base

    net = gross * (Decimal("1") - Decimal(rate_row.discount_pct))
    if rate_row.min_charge is not None and net < Decimal(rate_row.min_charge):
        net = Decimal(rate_row.min_charge)
    if rate_row.max_charge is not None and net > Decimal(rate_row.max_charge):
        net = Decimal(rate_row.max_charge)
    return money(max(net, ZERO))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class _DuplicateIndex:
    """Cross-invoice state the duplicate rule needs.

    Invoices are indexed in (date, number) order, so the first entry under a
    key is the original and anything after it is a duplicate.
    """

    exact: dict[tuple, list[CarrierInvoice]] = field(default_factory=dict)

    @classmethod
    def build(cls, invoices: Sequence[CarrierInvoice]) -> _DuplicateIndex:
        index = cls()
        for invoice in sorted(invoices, key=_invoice_order):
            if not invoice.shipment_ref:
                continue
            key = (
                invoice.carrier_scac.upper(),
                invoice.shipment_ref.upper(),
                money(invoice.total_amount),
            )
            index.exact.setdefault(key, []).append(invoice)
        return index

    def earlier_duplicate(self, invoice: CarrierInvoice) -> CarrierInvoice | None:
        if not invoice.shipment_ref:
            return None
        key = (
            invoice.carrier_scac.upper(),
            invoice.shipment_ref.upper(),
            money(invoice.total_amount),
        )
        for candidate in self.exact.get(key, []):
            if candidate.invoice_number != invoice.invoice_number:
                return candidate
            break  # the first entry is the original; stop at ourselves
        return None


def _invoice_order(invoice: CarrierInvoice) -> tuple:
    return (invoice.invoice_date, str(invoice.invoice_number))


class AuditEngine:
    """Applies every rule in :mod:`app.services.rules` to carrier invoices."""

    def __init__(
        self,
        rate_book: RateBook,
        *,
        amount_tolerance: Decimal | None = None,
        percent_tolerance: Decimal | None = None,
    ) -> None:
        self.rate_book = rate_book
        self.amount_tolerance = Decimal(
            amount_tolerance if amount_tolerance is not None else settings.amount_tolerance
        )
        self.percent_tolerance = Decimal(
            percent_tolerance if percent_tolerance is not None else settings.percent_tolerance
        )

    # -- public API --------------------------------------------------------

    def audit_batch(
        self, invoices: Sequence[CarrierInvoice]
    ) -> dict[str, list[AuditFinding]]:
        """Audit a set of invoices, keyed by ``carrier/invoice_number``.

        Duplicate detection only works across a batch, so this — not
        :meth:`audit_invoice` — is the entry point the API uses.
        """
        duplicates = _DuplicateIndex.build(invoices)
        return {
            invoice_key(invoice): self.audit_invoice(invoice, duplicates=duplicates)
            for invoice in sorted(invoices, key=_invoice_order)
        }

    def audit_invoice(
        self,
        invoice: CarrierInvoice,
        *,
        duplicates: _DuplicateIndex | None = None,
    ) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        currency = invoice.currency or "USD"

        if duplicates is not None:
            findings.extend(self._check_duplicate(invoice, duplicates))

        shipment = invoice.shipment
        if shipment is None:
            findings.append(
                AuditFinding(
                    rule_code="MISSING_SHIPMENT",
                    charge_code="",
                    billed_amount=money(invoice.total_amount),
                    expected_amount=money(invoice.total_amount),
                    message=(
                        f"Invoice {invoice.invoice_number} references "
                        f"'{invoice.shipment_ref or 'no shipment'}', which is not in the "
                        "shipment of record."
                    ),
                    recommendation=(
                        "Hold payment and request proof of delivery from the carrier "
                        "before releasing funds."
                    ),
                )
            )
            return _stamp(findings, currency)

        linehaul_rate = self.rate_book.find(
            carrier_scac=invoice.carrier_scac,
            mode=shipment.mode,
            charge_code=ChargeCode.FREIGHT.value,
            service_level=shipment.service_level,
            origin_state=shipment.origin_state,
            dest_state=shipment.dest_state,
            weight_lb=shipment.rated_weight_lb,
            when=shipment.ship_date,
        )
        billed_linehaul = money(invoice.amount_for(ChargeCode.FREIGHT.value))

        if linehaul_rate is None:
            # Nothing to price against, so the linehaul is taken at face value
            # and fuel is measured against what was actually billed.
            expected_linehaul = billed_linehaul
        else:
            expected_linehaul = expected_charge(
                linehaul_rate,
                weight_lb=shipment.rated_weight_lb,
                distance_mi=shipment.distance_mi,
            )
            findings.extend(
                self._check_linehaul(shipment, linehaul_rate, billed_linehaul, expected_linehaul)
            )

        findings.extend(self._check_fuel(invoice, shipment, linehaul_rate, expected_linehaul))
        findings.extend(self._check_accessorials(invoice, shipment))

        return _stamp(findings, currency)

    # -- individual rules --------------------------------------------------

    def _check_duplicate(
        self, invoice: CarrierInvoice, duplicates: _DuplicateIndex
    ) -> list[AuditFinding]:
        original = duplicates.earlier_duplicate(invoice)
        if original is None:
            return []
        return [
            AuditFinding(
                rule_code="DUPLICATE_INVOICE",
                charge_code="",
                billed_amount=money(invoice.total_amount),
                expected_amount=ZERO,
                message=(
                    f"Invoice {invoice.invoice_number} duplicates "
                    f"{original.invoice_number} ({original.invoice_date}): same carrier, "
                    f"shipment {invoice.shipment_ref} and total {money(invoice.total_amount)}."
                ),
                recommendation=(
                    f"Reject in full; {original.invoice_number} already covers this shipment."
                ),
            )
        ]

    def _check_linehaul(
        self,
        shipment: Shipment,
        rate_row: ContractedRate,
        billed: Decimal,
        expected: Decimal,
    ) -> list[AuditFinding]:
        variance = money(billed - expected)
        if variance <= self.amount_tolerance:
            return []
        if expected > ZERO and variance / expected <= self.percent_tolerance:
            return []

        discount = Decimal(rate_row.discount_pct)
        basis = f"{rate_row.rate_type} {rate_row.base_rate}" + (
            f" less {discount * 100:.2f}% discount" if discount else ""
        )
        return [
            AuditFinding(
                rule_code="RATE_MISMATCH",
                charge_code=ChargeCode.FREIGHT.value,
                billed_amount=billed,
                expected_amount=expected,
                message=(
                    f"Linehaul billed {billed} against contracted {expected} "
                    f"({basis}) for {shipment.rated_weight_lb} lb "
                    f"{shipment.origin_state}->{shipment.dest_state}."
                ),
                recommendation=(
                    f"Dispute {variance} on contract {rate_row.contract_id or rate_row.id}."
                ),
            )
        ]

    def _check_fuel(
        self,
        invoice: CarrierInvoice,
        shipment: Shipment,
        linehaul_rate: ContractedRate | None,
        expected_linehaul: Decimal,
    ) -> list[AuditFinding]:
        billed_fuel = money(invoice.amount_for(ChargeCode.FUEL.value))
        if billed_fuel <= ZERO:
            return []

        fuel_rate = self.rate_book.find(
            carrier_scac=invoice.carrier_scac,
            mode=shipment.mode,
            charge_code=ChargeCode.FUEL.value,
            service_level=shipment.service_level,
            origin_state=shipment.origin_state,
            dest_state=shipment.dest_state,
            weight_lb=shipment.rated_weight_lb,
            when=shipment.ship_date,
        )
        if fuel_rate is not None:
            expected_fuel = expected_charge(fuel_rate, basis=expected_linehaul)
            pct_label = f"{Decimal(fuel_rate.base_rate) * 100:.2f}% of linehaul"
        elif linehaul_rate is not None and Decimal(linehaul_rate.fuel_surcharge_pct) > ZERO:
            pct = Decimal(linehaul_rate.fuel_surcharge_pct)
            expected_fuel = money(expected_linehaul * pct)
            pct_label = f"{pct * 100:.2f}% of linehaul"
        else:
            return [
                AuditFinding(
                    rule_code="UNCONTRACTED_CHARGE",
                    charge_code=ChargeCode.FUEL.value,
                    billed_amount=billed_fuel,
                    expected_amount=ZERO,
                    message=(
                        f"Fuel surcharge of {billed_fuel} billed with no contracted fuel "
                        f"table for {invoice.carrier_scac}."
                    ),
                    recommendation="Dispute in full until a fuel schedule is on file.",
                )
            ]

        variance = money(billed_fuel - expected_fuel)
        if variance <= self.amount_tolerance:
            return []
        if expected_fuel > ZERO and variance / expected_fuel <= self.percent_tolerance:
            return []
        return [
            AuditFinding(
                rule_code="FUEL_SURCHARGE_VARIANCE",
                charge_code=ChargeCode.FUEL.value,
                billed_amount=billed_fuel,
                expected_amount=expected_fuel,
                message=(
                    f"Fuel billed {billed_fuel}; contract allows {expected_fuel} "
                    f"({pct_label} of {expected_linehaul})."
                ),
                recommendation=f"Dispute {variance} of fuel surcharge.",
            )
        ]

    def _check_accessorials(
        self, invoice: CarrierInvoice, shipment: Shipment
    ) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        ordered = shipment.ordered_accessorials

        # Aggregate per charge code: one finding per code keeps the exception
        # list readable and matches the uq_exception_key constraint.
        grouped: dict[str, Decimal] = {}
        for line in invoice.line_items:
            if line.charge_code not in ACCESSORIAL_CODES:
                continue
            grouped[line.charge_code] = grouped.get(line.charge_code, ZERO) + money(line.amount)

        for charge_code, billed in sorted(grouped.items()):
            if charge_code not in ordered:
                findings.append(
                    AuditFinding(
                        rule_code="ACCESSORIAL_NOT_ORDERED",
                        charge_code=charge_code,
                        billed_amount=billed,
                        expected_amount=ZERO,
                        message=(
                            f"{charge_code} billed at {billed} but shipment "
                            f"{shipment.shipment_id} ordered "
                            f"{', '.join(sorted(ordered)) or 'no accessorials'}."
                        ),
                        recommendation=f"Dispute the full {billed}; service was not requested.",
                    )
                )
                continue

            rate_row = self.rate_book.find(
                carrier_scac=invoice.carrier_scac,
                mode=shipment.mode,
                charge_code=charge_code,
                service_level=shipment.service_level,
                origin_state=shipment.origin_state,
                dest_state=shipment.dest_state,
                weight_lb=shipment.rated_weight_lb,
                when=shipment.ship_date,
            )
            if rate_row is None:
                findings.append(
                    AuditFinding(
                        rule_code="UNCONTRACTED_CHARGE",
                        charge_code=charge_code,
                        billed_amount=billed,
                        expected_amount=ZERO,
                        message=(
                            f"{charge_code} billed at {billed} with no contracted rate for "
                            f"{invoice.carrier_scac} {shipment.mode}."
                        ),
                        recommendation=(
                            "Dispute in full or negotiate the accessorial into the contract."
                        ),
                    )
                )
        return findings


def _stamp(findings: list[AuditFinding], currency: str) -> list[AuditFinding]:
    for finding in findings:
        finding.currency = currency
    return findings


def invoice_key(invoice: CarrierInvoice) -> str:
    return f"{invoice.carrier_scac}/{invoice.invoice_number}"


# ---------------------------------------------------------------------------
# Persistence wrapper
# ---------------------------------------------------------------------------


def _scoped_invoices(db: Session, request: AuditRunRequest) -> list[CarrierInvoice]:
    stmt = (
        select(CarrierInvoice)
        .options(selectinload(CarrierInvoice.line_items), selectinload(CarrierInvoice.shipment))
        .order_by(CarrierInvoice.invoice_date, CarrierInvoice.invoice_number)
    )
    if request.carrier_scac:
        stmt = stmt.where(CarrierInvoice.carrier_scac == request.carrier_scac.upper())
    if request.invoice_ids:
        stmt = stmt.where(CarrierInvoice.id.in_(request.invoice_ids))
    if request.invoiced_from:
        stmt = stmt.where(CarrierInvoice.invoice_date >= request.invoiced_from)
    if request.invoiced_to:
        stmt = stmt.where(CarrierInvoice.invoice_date <= request.invoiced_to)
    return list(db.scalars(stmt).all())


def run_audit(db: Session, request: AuditRunRequest | None = None) -> AuditRun:
    """Audit every invoice in scope and persist the run and its exceptions."""
    request = request or AuditRunRequest()
    invoices = _scoped_invoices(db, request)

    run = AuditRun(triggered_by=request.triggered_by, status=AuditRunStatus.RUNNING)
    db.add(run)
    db.flush()

    if request.replace_existing and invoices:
        invoice_ids = [invoice.id for invoice in invoices]
        db.query(AuditException).filter(
            AuditException.invoice_id.in_(invoice_ids),
            AuditException.run_id != run.id,
        ).delete(synchronize_session=False)

    engine = AuditEngine(RateBook.from_db(db))
    findings_by_invoice = engine.audit_batch(invoices)

    total_billed = ZERO
    total_variance = ZERO
    exceptions_found = 0

    for invoice in invoices:
        total_billed += money(invoice.total_amount)
        findings = findings_by_invoice.get(invoice_key(invoice), [])
        for finding in findings:
            db.add(
                AuditException(
                    run_id=run.id,
                    invoice_id=invoice.id,
                    shipment_id=invoice.shipment_id,
                    rule_code=finding.rule_code,
                    rule_name=finding.rule_name,
                    severity=finding.resolved_severity,
                    charge_code=finding.charge_code,
                    billed_amount=finding.billed_amount,
                    expected_amount=finding.expected_amount,
                    variance_amount=finding.variance_amount,
                    currency=finding.currency,
                    message=finding.message,
                    recommendation=finding.recommendation,
                )
            )
            total_variance += finding.variance_amount
            exceptions_found += 1
        invoice.status = InvoiceStatus.DISPUTED if findings else InvoiceStatus.AUDITED

    run.invoices_audited = len(invoices)
    run.exceptions_found = exceptions_found
    run.total_billed = money(total_billed)
    run.total_variance = money(total_variance)
    run.status = AuditRunStatus.COMPLETED
    run.completed_at = utcnow()
    run.notes = (
        f"{exceptions_found} exception(s) across {len(invoices)} invoice(s); "
        f"{money(total_variance)} recoverable."
    )

    db.commit()
    db.refresh(run)
    return run


__all__ = [
    "RULES",
    "AuditEngine",
    "AuditFinding",
    "RateBook",
    "expected_charge",
    "invoice_key",
    "money",
    "run_audit",
]
