"""Spend intelligence: the aggregate queries behind the dashboard."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.models import AuditException, CarrierInvoice, ExceptionStatus, Shipment
from app.schemas.analytics import CarrierScorecard, KpiSummary, RuleBreakdown
from app.services.audit_engine import money
from app.services.rules import MONETARY_RULES, RULES

ZERO = Decimal("0")
RATE_PRECISION = Decimal("0.0001")


def _apply_window(stmt: Select, column, start: date | None, end: date | None) -> Select:
    if start:
        stmt = stmt.where(column >= start)
    if end:
        stmt = stmt.where(column <= end)
    return stmt


def _dec(value) -> Decimal:
    return money(value) if value is not None else ZERO


def _is(status, member: ExceptionStatus) -> bool:
    """Compare a status column that may come back as an enum or a plain string."""
    return status in (member, member.value)


def variance_by_rule(
    db: Session, *, start: date | None = None, end: date | None = None
) -> list[RuleBreakdown]:
    """Exception count and dollars per rule, largest variance first."""
    stmt = (
        select(
            AuditException.rule_code,
            func.count(AuditException.id),
            func.coalesce(func.sum(AuditException.variance_amount), 0),
        )
        .join(CarrierInvoice, AuditException.invoice_id == CarrierInvoice.id)
        .group_by(AuditException.rule_code)
        .order_by(func.sum(AuditException.variance_amount).desc())
    )
    stmt = _apply_window(stmt, CarrierInvoice.invoice_date, start, end)

    rows = []
    for code, count, variance in db.execute(stmt).all():
        spec = RULES.get(code)
        rows.append(
            RuleBreakdown(
                rule_code=code,
                rule_name=spec.name if spec else code,
                severity=spec.severity if spec else "medium",
                count=count,
                variance_amount=_dec(variance),
            )
        )
    return rows


def kpi_summary(
    db: Session, *, start: date | None = None, end: date | None = None
) -> KpiSummary:
    """Headline numbers for the dashboard."""
    invoices, total_billed = db.execute(
        _apply_window(
            select(
                func.count(CarrierInvoice.id),
                func.coalesce(func.sum(CarrierInvoice.total_amount), 0),
            ),
            CarrierInvoice.invoice_date,
            start,
            end,
        )
    ).one()
    total_billed = _dec(total_billed)

    shipments = db.scalar(
        _apply_window(select(func.count(Shipment.id)), Shipment.ship_date, start, end)
    ) or 0

    exc_stmt = _apply_window(
        select(
            AuditException.status,
            AuditException.rule_code,
            func.count(AuditException.id),
            func.coalesce(func.sum(AuditException.variance_amount), 0),
        )
        .join(CarrierInvoice, AuditException.invoice_id == CarrierInvoice.id)
        .group_by(AuditException.status, AuditException.rule_code),
        CarrierInvoice.invoice_date,
        start,
        end,
    )

    exceptions = 0
    total_variance = ZERO
    recoverable = ZERO
    recovered = ZERO
    for status, rule_code, count, variance in db.execute(exc_stmt).all():
        variance = _dec(variance)
        exceptions += count
        if rule_code in MONETARY_RULES:
            total_variance += variance
        if _is(status, ExceptionStatus.OPEN) or _is(status, ExceptionStatus.DISPUTED):
            recoverable += max(variance, ZERO)
        elif _is(status, ExceptionStatus.RECOVERED):
            recovered += max(variance, ZERO)

    flagged = db.scalar(
        _apply_window(
            select(func.count(func.distinct(AuditException.invoice_id))).join(
                CarrierInvoice, AuditException.invoice_id == CarrierInvoice.id
            ),
            CarrierInvoice.invoice_date,
            start,
            end,
        )
    ) or 0

    return KpiSummary(
        shipments=shipments,
        invoices_audited=invoices,
        exceptions_detected=exceptions,
        total_billed=total_billed,
        total_expected=money(total_billed - total_variance),
        total_variance=money(total_variance),
        recoverable_amount=money(recoverable),
        recovered_amount=money(recovered),
        exception_rate=(
            Decimal(flagged) / Decimal(invoices) if invoices else ZERO
        ).quantize(RATE_PRECISION),
        overcharge_rate=(
            total_variance / total_billed if total_billed else ZERO
        ).quantize(RATE_PRECISION),
        average_cost_per_shipment=money(total_billed / shipments) if shipments else ZERO,
        by_rule=variance_by_rule(db, start=start, end=end),
    )


def carrier_scorecards(
    db: Session, *, start: date | None = None, end: date | None = None
) -> list[CarrierScorecard]:
    """Spend, billing accuracy and service performance per carrier.

    Three small aggregates merged in Python rather than one wide join: the
    invoice and exception windows key off ``invoice_date`` while the shipment
    window keys off ``ship_date``, and an outer join across both would either
    drop carriers or double-count their spend.
    """
    billed = {
        scac: (count, _dec(total))
        for scac, count, total in db.execute(
            _apply_window(
                select(
                    CarrierInvoice.carrier_scac,
                    func.count(CarrierInvoice.id),
                    func.coalesce(func.sum(CarrierInvoice.total_amount), 0),
                ).group_by(CarrierInvoice.carrier_scac),
                CarrierInvoice.invoice_date,
                start,
                end,
            )
        ).all()
    }

    exceptions: dict[str, tuple[int, Decimal, Decimal]] = {}
    exc_rows = db.execute(
        _apply_window(
            select(
                CarrierInvoice.carrier_scac,
                AuditException.rule_code,
                AuditException.status,
                func.count(AuditException.id),
                func.coalesce(func.sum(AuditException.variance_amount), 0),
            )
            .join(CarrierInvoice, AuditException.invoice_id == CarrierInvoice.id)
            .group_by(CarrierInvoice.carrier_scac, AuditException.rule_code, AuditException.status),
            CarrierInvoice.invoice_date,
            start,
            end,
        )
    ).all()
    for scac, rule_code, status, count, variance in exc_rows:
        found, variance_total, recoverable = exceptions.get(scac, (0, ZERO, ZERO))
        variance = _dec(variance)
        found += count
        if rule_code in MONETARY_RULES:
            variance_total += variance
            if _is(status, ExceptionStatus.OPEN) or _is(status, ExceptionStatus.DISPUTED):
                recoverable += max(variance, ZERO)
        exceptions[scac] = (found, variance_total, recoverable)

    service = {
        scac: (count, name, on_time, measurable)
        for scac, count, name, on_time, measurable in db.execute(
            _apply_window(
                select(
                    Shipment.carrier_scac,
                    func.count(Shipment.id),
                    func.max(Shipment.carrier_name),
                    # Both dates have to be on file for lateness to mean anything.
                    func.sum(
                        case(
                            (Shipment.delivery_date <= Shipment.promised_date, 1),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                Shipment.delivery_date.is_not(None)
                                & Shipment.promised_date.is_not(None),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                ).group_by(Shipment.carrier_scac),
                Shipment.ship_date,
                start,
                end,
            )
        ).all()
    }

    cards = []
    for scac in sorted(set(billed) | set(service)):
        invoices, total_billed = billed.get(scac, (0, ZERO))
        found, variance_total, recoverable = exceptions.get(scac, (0, ZERO, ZERO))
        shipments, carrier_name, on_time, measurable = service.get(scac, (0, None, 0, 0))
        cards.append(
            CarrierScorecard(
                carrier_scac=scac,
                carrier_name=carrier_name,
                shipments=shipments,
                invoices=invoices,
                total_billed=money(total_billed),
                exceptions=found,
                total_variance=money(variance_total),
                recoverable_amount=money(recoverable),
                variance_rate=(
                    variance_total / total_billed if total_billed else ZERO
                ).quantize(RATE_PRECISION),
                on_time_rate=(
                    (Decimal(on_time or 0) / Decimal(measurable)).quantize(RATE_PRECISION)
                    if measurable
                    else None
                ),
            )
        )
    # Worst dollars first: that is the order a recovery analyst works in.
    cards.sort(key=lambda card: (card.total_variance, card.total_billed), reverse=True)
    return cards


__all__ = ["carrier_scorecards", "kpi_summary", "variance_by_rule"]
