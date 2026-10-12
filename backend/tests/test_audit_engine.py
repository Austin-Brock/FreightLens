"""Rate pricing, rate-book lookup and every audit rule."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import AuditException, ExceptionSeverity, RateType, TransportMode
from app.schemas.audit import AuditRunRequest
from app.services.audit_engine import (
    AuditEngine,
    RateBook,
    UnpriceableCharge,
    expected_charge,
    money,
    run_audit,
)
from tests.conftest import make_invoice, make_rate, make_shipment


def engine(*rates, **kwargs) -> AuditEngine:
    return AuditEngine(RateBook(rates), **kwargs)


def codes(findings) -> list[str]:
    return [finding.rule_code for finding in findings]


def only(findings, rule_code):
    matching = [finding for finding in findings if finding.rule_code == rule_code]
    assert len(matching) == 1, f"expected exactly one {rule_code}, got {codes(findings)}"
    return matching[0]


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_per_cwt_applies_discount_then_minimum_charge():
    rate = make_rate(base_rate=Decimal("40.00"), discount_pct=Decimal("0.25"), min_charge=Decimal("200.00"))
    # 10 cwt x 40.00 = 400.00, less 25% = 300.00; above the 200.00 minimum.
    assert expected_charge(rate, weight_lb=Decimal("1000")) == Decimal("300.00")
    # 2 cwt x 40.00 = 80.00, less 25% = 60.00; the minimum charge takes over.
    assert expected_charge(rate, weight_lb=Decimal("200")) == Decimal("200.00")


def test_max_charge_caps_the_result():
    rate = make_rate(base_rate=Decimal("40.00"), max_charge=Decimal("250.00"))
    assert expected_charge(rate, weight_lb=Decimal("1000")) == Decimal("250.00")


@pytest.mark.parametrize(
    ("rate_type", "base", "kwargs", "expected"),
    [
        (RateType.PER_LB, "0.95", {"weight_lb": Decimal("20")}, "19.00"),
        (RateType.PER_MILE, "2.45", {"distance_mi": Decimal("1150")}, "2817.50"),
        (RateType.FLAT, "65.00", {}, "65.00"),
        (RateType.PCT_OF_LINEHAUL, "0.22", {"basis": Decimal("500")}, "110.00"),
    ],
)
def test_each_rate_type_prices_from_its_own_driver(rate_type, base, kwargs, expected):
    rate = make_rate(rate_type=rate_type, base_rate=Decimal(base))
    assert expected_charge(rate, **kwargs) == Decimal(expected)


def test_a_rate_whose_driver_is_missing_is_unpriceable():
    """A minimum charge is a floor on a priced move, not a price for an unpriced one."""
    per_mile = make_rate(
        rate_type=RateType.PER_MILE, base_rate=Decimal("2.45"), min_charge=Decimal("650.00")
    )
    with pytest.raises(UnpriceableCharge, match="no distance"):
        expected_charge(per_mile, distance_mi=None)

    per_cwt = make_rate(base_rate=Decimal("30.00"), min_charge=Decimal("95.00"))
    with pytest.raises(UnpriceableCharge, match="no weight"):
        expected_charge(per_cwt, weight_lb=Decimal("0"))


def test_a_shipment_with_no_distance_is_not_claimed_against_a_per_mile_rate(db):
    """Regression: the minimum charge used to become the "expected" linehaul.

    A 1,150-mile truckload at $2.45/mi is $2,817.50, but with the distance
    missing from the shipment feed the rate priced at its $650 minimum and the
    audit claimed the $2,167.50 difference from a carrier that billed
    correctly.
    """
    rate = make_rate(
        mode=TransportMode.FTL,
        rate_type=RateType.PER_MILE,
        base_rate=Decimal("2.45"),
        min_charge=Decimal("650.00"),
    )
    shipment = make_shipment(db, mode=TransportMode.FTL, distance_mi=None)
    invoice = make_invoice(db, shipment=shipment, lines=[("FREIGHT", "2817.50")])

    findings = engine(rate).audit_invoice(invoice)
    assert codes(findings) == []


def test_a_distance_still_audits_normally(db):
    """The same lane, with the distance present, is audited as before."""
    rate = make_rate(
        mode=TransportMode.FTL,
        rate_type=RateType.PER_MILE,
        base_rate=Decimal("2.45"),
        min_charge=Decimal("650.00"),
    )
    shipment = make_shipment(db, mode=TransportMode.FTL, distance_mi=Decimal("1150"))
    invoice = make_invoice(db, shipment=shipment, lines=[("FREIGHT", "3200.00")])

    finding = only(engine(rate).audit_invoice(invoice), "RATE_MISMATCH")
    assert finding.expected_amount == Decimal("2817.50")
    assert finding.variance_amount == Decimal("382.50")


# ---------------------------------------------------------------------------
# Rate book lookup
# ---------------------------------------------------------------------------


def test_lane_specific_rate_beats_the_wildcard_rate():
    national = make_rate(base_rate=Decimal("36.00"))
    lane = make_rate(base_rate=Decimal("32.00"), origin_state="CA", dest_state="TX")
    book = RateBook([national, lane])

    found = book.find(
        carrier_scac="ODFL", mode=TransportMode.LTL, charge_code="FREIGHT",
        origin_state="CA", dest_state="TX", weight_lb=Decimal("1000"),
    )
    assert found is lane


def test_weight_breaks_select_the_right_row():
    light = make_rate(base_rate=Decimal("38.00"), min_weight_lb=Decimal("0"), max_weight_lb=Decimal("499"))
    heavy = make_rate(base_rate=Decimal("22.00"), min_weight_lb=Decimal("500"), max_weight_lb=Decimal("4999"))
    book = RateBook([light, heavy])

    assert book.find(carrier_scac="ODFL", mode="ltl", charge_code="FREIGHT", weight_lb=Decimal("300")) is light
    assert book.find(carrier_scac="ODFL", mode="ltl", charge_code="FREIGHT", weight_lb=Decimal("3000")) is heavy
    assert book.find(carrier_scac="ODFL", mode="ltl", charge_code="FREIGHT", weight_lb=Decimal("9000")) is None


def test_expired_rates_are_not_returned():
    expired = make_rate(effective_date=date(2023, 1, 1), expiration_date=date(2024, 12, 31))
    book = RateBook([expired])
    assert book.find(carrier_scac="ODFL", mode="ltl", charge_code="FREIGHT", when=date(2026, 3, 2)) is None
    assert book.find(carrier_scac="ODFL", mode="ltl", charge_code="FREIGHT", when=date(2024, 6, 1)) is expired


def test_lookup_is_scoped_to_carrier_and_mode():
    rate = make_rate()
    book = RateBook([rate])
    assert book.find(carrier_scac="XPOL", mode="ltl", charge_code="FREIGHT") is None
    assert book.find(carrier_scac="ODFL", mode="ftl", charge_code="FREIGHT") is None
    assert book.has_carrier("odfl") is True


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def test_a_correctly_billed_invoice_produces_no_findings():
    shipment = make_shipment(accessorials="LIFTGATE")
    freight = make_rate(base_rate=Decimal("30.00"))
    fuel = make_rate(charge_code="FUEL", rate_type=RateType.PCT_OF_LINEHAUL, base_rate=Decimal("0.20"))
    liftgate = make_rate(charge_code="LIFTGATE", rate_type=RateType.FLAT, base_rate=Decimal("95.00"))
    invoice = make_invoice(
        shipment=shipment,
        lines=[("FREIGHT", "300.00"), ("FUEL", "60.00"), ("LIFTGATE", "95.00")],
    )

    assert engine(freight, fuel, liftgate).audit_invoice(invoice) == []


def test_linehaul_over_contract_is_a_rate_mismatch():
    shipment = make_shipment()
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "420.00")])

    finding = only(engine(make_rate(base_rate=Decimal("30.00"))).audit_invoice(invoice), "RATE_MISMATCH")
    assert (finding.billed_amount, finding.expected_amount, finding.variance_amount) == (
        Decimal("420.00"), Decimal("300.00"), Decimal("120.00"),
    )
    assert finding.resolved_severity == ExceptionSeverity.HIGH


def test_variance_inside_the_dollar_tolerance_is_ignored():
    shipment = make_shipment()
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "300.75")])
    assert engine(make_rate(base_rate=Decimal("30.00"))).audit_invoice(invoice) == []


def test_variance_inside_the_percent_tolerance_is_ignored():
    shipment = make_shipment(weight_lb=Decimal("10000"))
    rate = make_rate(base_rate=Decimal("30.00"))  # expects 3,000.00
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "3050.00")])

    assert engine(rate).audit_invoice(invoice) == []
    # Past 2% the same shape of variance is reported.
    assert "RATE_MISMATCH" in codes(
        engine(rate).audit_invoice(make_invoice(shipment=shipment, lines=[("FREIGHT", "3200.00")]))
    )


def test_an_unmatched_invoice_stops_the_audit_with_one_finding():
    invoice = make_invoice(lines=[("FREIGHT", "742.00")], shipment_ref="SHP-19999")

    findings = engine(make_rate()).audit_invoice(invoice)
    assert codes(findings) == ["MISSING_SHIPMENT"]
    assert findings[0].variance_amount == Decimal("0.00")
    assert "SHP-19999" in findings[0].message


def test_fuel_above_the_contracted_percentage_is_flagged():
    shipment = make_shipment()
    freight = make_rate(base_rate=Decimal("30.00"))
    fuel = make_rate(charge_code="FUEL", rate_type=RateType.PCT_OF_LINEHAUL, base_rate=Decimal("0.20"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "300.00"), ("FUEL", "90.00")])

    finding = only(engine(freight, fuel).audit_invoice(invoice), "FUEL_SURCHARGE_VARIANCE")
    assert (finding.expected_amount, finding.variance_amount) == (Decimal("60.00"), Decimal("30.00"))


def test_fuel_falls_back_to_the_percentage_on_the_linehaul_rate():
    shipment = make_shipment()
    freight = make_rate(base_rate=Decimal("30.00"), fuel_surcharge_pct=Decimal("0.20"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "300.00"), ("FUEL", "61.00")])

    assert codes(engine(freight).audit_invoice(invoice)) == []


def test_fuel_with_no_contracted_schedule_is_disputed_in_full():
    shipment = make_shipment()
    freight = make_rate(base_rate=Decimal("30.00"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "300.00"), ("FUEL", "66.00")])

    finding = only(engine(freight).audit_invoice(invoice), "UNCONTRACTED_CHARGE")
    assert (finding.charge_code, finding.variance_amount) == ("FUEL", Decimal("66.00"))


def test_fuel_is_priced_off_the_contracted_linehaul_not_the_billed_one():
    """An inflated linehaul must not legitimise the fuel built on top of it."""
    shipment = make_shipment()
    freight = make_rate(base_rate=Decimal("30.00"))
    fuel = make_rate(charge_code="FUEL", rate_type=RateType.PCT_OF_LINEHAUL, base_rate=Decimal("0.20"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "400.00"), ("FUEL", "80.00")])

    findings = engine(freight, fuel).audit_invoice(invoice)
    assert only(findings, "RATE_MISMATCH").variance_amount == Decimal("100.00")
    assert only(findings, "FUEL_SURCHARGE_VARIANCE").variance_amount == Decimal("20.00")


def test_accessorial_that_was_never_ordered_is_disputed_in_full():
    shipment = make_shipment(accessorials=None)
    freight = make_rate(base_rate=Decimal("30.00"))
    resi = make_rate(charge_code="RESIDENTIAL", rate_type=RateType.FLAT, base_rate=Decimal("120.00"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "300.00"), ("RESIDENTIAL", "120.00")])

    finding = only(engine(freight, resi).audit_invoice(invoice), "ACCESSORIAL_NOT_ORDERED")
    assert finding.variance_amount == Decimal("120.00")
    assert "no accessorials" in finding.message


def test_ordered_accessorial_with_no_contract_rate_is_uncontracted():
    shipment = make_shipment(accessorials="LIFTGATE")
    freight = make_rate(base_rate=Decimal("30.00"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "300.00"), ("LIFTGATE", "125.00")])

    finding = only(engine(freight).audit_invoice(invoice), "UNCONTRACTED_CHARGE")
    assert (finding.charge_code, finding.variance_amount) == ("LIFTGATE", Decimal("125.00"))


def test_a_second_identical_invoice_is_a_duplicate_and_the_original_is_clean():
    shipment = make_shipment()
    freight = make_rate(base_rate=Decimal("30.00"))
    original = make_invoice(shipment=shipment, invoice_number="INV-1", lines=[("FREIGHT", "300.00")])
    duplicate = make_invoice(
        shipment=shipment, invoice_number="INV-2", lines=[("FREIGHT", "300.00")],
        invoice_date=date(2026, 3, 18),
    )

    results = engine(freight).audit_batch([duplicate, original])
    assert codes(results["ODFL/INV-1"]) == []
    finding = only(results["ODFL/INV-2"], "DUPLICATE_INVOICE")
    assert finding.variance_amount == Decimal("300.00")
    assert "INV-1" in finding.message


def test_billable_weight_overrides_actual_weight_when_present():
    shipment = make_shipment(weight_lb=Decimal("1000"), billable_weight_lb=Decimal("2000"))
    freight = make_rate(base_rate=Decimal("30.00"))
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "600.00")])
    assert codes(engine(freight).audit_invoice(invoice)) == []


def test_findings_inherit_the_invoice_currency():
    shipment = make_shipment()
    invoice = make_invoice(shipment=shipment, lines=[("FREIGHT", "420.00")], currency="CAD")
    assert only(engine(make_rate(base_rate=Decimal("30.00"))).audit_invoice(invoice), "RATE_MISMATCH").currency == "CAD"


def test_money_rounds_half_up_the_way_carriers_do():
    assert money("0.125") == Decimal("0.13")
    assert money(Decimal("2.675")) == Decimal("2.68")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_run_audit_persists_a_run_and_its_exceptions(db):
    shipment = make_shipment(db)
    make_rate(db, base_rate=Decimal("30.00"))
    make_invoice(db, shipment=shipment, lines=[("FREIGHT", "420.00")])
    db.commit()

    run = run_audit(db)

    assert (run.invoices_audited, run.exceptions_found) == (1, 1)
    assert run.total_billed == Decimal("420.00")
    assert run.total_variance == Decimal("120.00")
    assert run.status == "completed"
    assert run.completed_at is not None

    exception = db.query(AuditException).one()
    assert exception.rule_code == "RATE_MISMATCH"
    assert exception.status == "open"
    assert exception.invoice_id is not None and exception.shipment_id is not None


def test_rerunning_the_audit_replaces_the_previous_exceptions(db):
    shipment = make_shipment(db)
    make_rate(db, base_rate=Decimal("30.00"))
    make_invoice(db, shipment=shipment, lines=[("FREIGHT", "420.00")])
    db.commit()

    run_audit(db)
    second = run_audit(db)

    assert db.query(AuditException).count() == 1
    assert db.query(AuditException).one().run_id == second.id


def test_keeping_history_is_opt_in(db):
    shipment = make_shipment(db)
    make_rate(db, base_rate=Decimal("30.00"))
    make_invoice(db, shipment=shipment, lines=[("FREIGHT", "420.00")])
    db.commit()

    run_audit(db)
    run_audit(db, AuditRunRequest(replace_existing=False))
    assert db.query(AuditException).count() == 2


def test_run_audit_can_be_scoped_to_one_carrier(db):
    odfl = make_shipment(db, shipment_id="SHP-1", carrier_scac="ODFL")
    xpol = make_shipment(db, shipment_id="SHP-2", carrier_scac="XPOL")
    make_rate(db, base_rate=Decimal("30.00"))
    make_rate(db, carrier_scac="XPOL", base_rate=Decimal("30.00"))
    make_invoice(db, shipment=odfl, invoice_number="INV-1", lines=[("FREIGHT", "420.00")])
    make_invoice(db, shipment=xpol, invoice_number="INV-2", lines=[("FREIGHT", "420.00")])
    db.commit()

    run = run_audit(db, AuditRunRequest(carrier_scac="xpol"))
    assert run.invoices_audited == 1
    assert db.query(AuditException).one().invoice.carrier_scac == "XPOL"


def test_audited_invoices_are_marked_clean_or_disputed(db):
    shipment = make_shipment(db, shipment_id="SHP-1")
    clean_shipment = make_shipment(db, shipment_id="SHP-2")
    make_rate(db, base_rate=Decimal("30.00"))
    bad = make_invoice(db, shipment=shipment, invoice_number="INV-1", lines=[("FREIGHT", "420.00")])
    good = make_invoice(db, shipment=clean_shipment, invoice_number="INV-2", lines=[("FREIGHT", "300.00")])
    db.commit()

    run_audit(db)
    assert bad.status == "disputed"
    assert good.status == "audited"


def test_a_missed_guarantee_claims_the_transportation_charges(db):
    rate = make_rate(service_level="guaranteed", base_rate=Decimal("30.00"))
    shipment = make_shipment(
        db,
        service_level="guaranteed",
        promised_date=date(2026, 3, 5),
        delivery_date=date(2026, 3, 7),
    )
    invoice = make_invoice(
        db, shipment=shipment, lines=[("FREIGHT", "300.00"), ("FUEL", "66.00")]
    )

    finding = only(engine(rate).audit_invoice(invoice), "SERVICE_FAILURE")
    assert finding.variance_amount == Decimal("366.00")
    assert finding.resolved_severity == ExceptionSeverity.HIGH
    assert "2 days after" in finding.message


def test_a_late_delivery_on_standard_service_is_tracked_but_not_claimed(db):
    """A standard tariff promises a transit estimate, not a refund."""
    rate = make_rate(base_rate=Decimal("30.00"))
    shipment = make_shipment(
        db, promised_date=date(2026, 3, 5), delivery_date=date(2026, 3, 6)
    )
    invoice = make_invoice(
        db, shipment=shipment, lines=[("FREIGHT", "300.00"), ("FUEL", "66.00")]
    )

    finding = only(engine(rate).audit_invoice(invoice), "SERVICE_FAILURE")
    assert finding.variance_amount == Decimal("0.00")
    assert finding.resolved_severity == ExceptionSeverity.LOW
    assert "1 day after" in finding.message
    assert "No contractual remedy" in finding.recommendation


def test_a_guarantee_claim_does_not_also_re_rate_the_freight(db):
    """One disputed bill, one claim — you don't argue the rate on a bill you refuse."""
    rate = make_rate(service_level="guaranteed", base_rate=Decimal("30.00"))
    shipment = make_shipment(
        db,
        service_level="guaranteed",
        promised_date=date(2026, 3, 5),
        delivery_date=date(2026, 3, 7),
    )
    # Billed well above the 10 cwt x 30.00 = 300.00 contract, and late.
    invoice = make_invoice(
        db,
        shipment=shipment,
        lines=[("FREIGHT", "500.00"), ("FUEL", "110.00"), ("LIFTGATE", "95.00")],
    )

    findings = engine(rate).audit_invoice(invoice)
    assert sorted(codes(findings)) == ["ACCESSORIAL_NOT_ORDERED", "SERVICE_FAILURE"]
    # The whole transportation charge is claimed once, not re-rated on top.
    assert only(findings, "SERVICE_FAILURE").variance_amount == Decimal("610.00")
    assert only(findings, "ACCESSORIAL_NOT_ORDERED").variance_amount == Decimal("95.00")


def test_on_time_and_undated_deliveries_raise_nothing(db):
    rate = make_rate(service_level="guaranteed", base_rate=Decimal("30.00"))
    on_time = make_invoice(
        db,
        shipment=make_shipment(
            db,
            shipment_id="SHP-ONTIME",
            service_level="guaranteed",
            promised_date=date(2026, 3, 5),
            delivery_date=date(2026, 3, 5),
        ),
        lines=[("FREIGHT", "300.00")],
    )
    in_transit = make_invoice(
        db,
        invoice_number="INV-2",
        shipment=make_shipment(
            db,
            shipment_id="SHP-OPEN",
            service_level="guaranteed",
            promised_date=date(2026, 3, 5),
            delivery_date=None,
        ),
        lines=[("FREIGHT", "300.00")],
    )

    audit = engine(rate)
    assert "SERVICE_FAILURE" not in codes(audit.audit_invoice(on_time))
    assert "SERVICE_FAILURE" not in codes(audit.audit_invoice(in_transit))


def test_the_sample_data_exercises_every_rule(seeded_db):
    """The bundled fixtures are the engine's end-to-end regression case."""
    from app.services.rules import RULES

    run = run_audit(seeded_db)
    fired = {exc.rule_code for exc in seeded_db.query(AuditException).all()}

    assert fired == set(RULES), f"rules never exercised: {sorted(set(RULES) - fired)}"
    assert len(RULES) == 7
    assert run.invoices_audited == 27
    # Rate validation is the headline finding, which is what the demo narrates.
    by_rule = {}
    for exc in seeded_db.query(AuditException).all():
        by_rule[exc.rule_code] = by_rule.get(exc.rule_code, Decimal("0")) + exc.variance_amount
    assert max(by_rule, key=by_rule.get) == "RATE_MISMATCH"
