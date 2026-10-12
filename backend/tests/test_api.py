"""HTTP surface: filters, validation, audit workflow and analytics."""

from __future__ import annotations

from decimal import Decimal

from app.models import AuditException
from app.services.audit_engine import run_audit

V1 = "/api/v1"


def test_health_and_root():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    # "/" serves the dashboard, not JSON.
    root = client.get("/")
    assert root.status_code == 200
    assert "FreightLens" in root.text


# ---------------------------------------------------------------------------
# Shipments
# ---------------------------------------------------------------------------


def test_list_shipments_paginates(seeded_client):
    body = seeded_client.get(f"{V1}/shipments", params={"limit": 5}).json()
    assert body["total"] == 24
    assert len(body["items"]) == 5
    assert body["limit"] == 5 and body["offset"] == 0


def test_list_shipments_filters(seeded_client):
    odfl = seeded_client.get(f"{V1}/shipments", params={"carrier_scac": "odfl"}).json()
    assert odfl["total"] == 10
    assert {item["carrier_scac"] for item in odfl["items"]} == {"ODFL"}

    lane = seeded_client.get(
        f"{V1}/shipments", params={"origin_state": "ca", "dest_state": "tx"}
    ).json()
    assert lane["total"] == 3

    parcel = seeded_client.get(f"{V1}/shipments", params={"mode": "parcel"}).json()
    assert parcel["total"] == 6


def test_list_shipments_can_isolate_late_deliveries(seeded_client):
    body = seeded_client.get(f"{V1}/shipments", params={"late_only": True}).json()
    assert {item["shipment_id"] for item in body["items"]} == {
        "SHP-10008", "SHP-10009", "SHP-10024",
    }
    assert all(item["is_late"] for item in body["items"])


def test_get_shipment_exposes_derived_fields(seeded_client):
    body = seeded_client.get(f"{V1}/shipments/SHP-10001").json()
    assert body["transit_days"] == 3
    assert body["is_late"] is False
    assert body["accessorials"] == "LIFTGATE"


def test_get_unknown_shipment_is_404(seeded_client):
    assert seeded_client.get(f"{V1}/shipments/NOPE").status_code == 404


def test_create_shipment_normalises_and_rejects_duplicates(client):
    payload = {
        "shipment_id": "SHP-NEW", "carrier_scac": "odfl", "mode": "ltl",
        "origin_state": "ca", "origin_zip": "90021", "dest_state": "tx",
        "dest_zip": "75201", "ship_date": "2026-03-02", "weight_lb": "1000",
    }
    created = client.post(f"{V1}/shipments", json=payload)
    assert created.status_code == 201
    assert created.json()["carrier_scac"] == "ODFL"
    assert client.post(f"{V1}/shipments", json=payload).status_code == 409


def test_create_shipment_rejects_an_unknown_mode(client):
    response = client.post(
        f"{V1}/shipments",
        json={
            "shipment_id": "SHP-BAD", "carrier_scac": "ODFL", "mode": "teleport",
            "origin_state": "CA", "origin_zip": "90021", "dest_state": "TX",
            "dest_zip": "75201", "ship_date": "2026-03-02",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def test_list_invoices_includes_line_items(seeded_client):
    body = seeded_client.get(f"{V1}/invoices", params={"carrier_scac": "CHRW"}).json()
    assert body["total"] == 2
    assert all(item["line_items"] for item in body["items"])


def test_unmatched_only_surfaces_the_orphan_invoice(seeded_client):
    body = seeded_client.get(f"{V1}/invoices", params={"unmatched_only": True}).json()
    assert body["total"] == 1
    assert body["items"][0]["shipment_ref"] == "SHP-19999"


def test_create_invoice_links_the_shipment_and_totals_the_lines(seeded_client):
    response = seeded_client.post(
        f"{V1}/invoices",
        json={
            "invoice_number": "ODFL-NEW", "carrier_scac": "ODFL",
            "shipment_ref": "SHP-10001", "invoice_date": "2026-08-20",
            "line_items": [
                {"charge_code": "FREIGHT", "amount": "342.00", "quantity": "1200", "unit": "lb"},
                {"charge_code": "FUEL", "amount": "75.24"},
            ],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert Decimal(body["total_amount"]) == Decimal("417.24")
    assert body["shipment_id"] is not None
    assert [line["line_number"] for line in body["line_items"]] == [1, 2]

    assert seeded_client.post(
        f"{V1}/invoices",
        json={
            "invoice_number": "ODFL-NEW", "carrier_scac": "ODFL",
            "invoice_date": "2026-08-20", "line_items": [],
        },
    ).status_code == 409


def test_invoice_status_can_be_advanced(seeded_client):
    invoice_id = seeded_client.get(f"{V1}/invoices", params={"limit": 1}).json()["items"][0]["id"]
    response = seeded_client.patch(f"{V1}/invoices/{invoice_id}/status", json={"status": "paid"})
    assert response.status_code == 200
    assert response.json()["status"] == "paid"


def test_unknown_invoice_is_404(seeded_client):
    assert seeded_client.get(f"{V1}/invoices/99999").status_code == 404


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


def test_list_rates_filters_by_charge_code(seeded_client):
    body = seeded_client.get(
        f"{V1}/rates", params={"carrier_scac": "ODFL", "charge_code": "FREIGHT"}
    ).json()
    assert body["total"] == 5


def test_quote_prices_a_lane_from_the_contract(seeded_client):
    body = seeded_client.get(
        f"{V1}/rates/quote",
        params={
            "carrier_scac": "XPOL", "mode": "ltl", "origin_state": "CA",
            "dest_state": "TX", "weight_lb": "2500", "service_level": "standard",
            "ship_date": "2026-08-05",
        },
    ).json()
    # The CA-TX lane override wins over the national tariff: 32.00/cwt less 40%.
    assert Decimal(body["expected_amount"]) == Decimal("480.00")
    assert body["rate"]["contract_id"] == "XPO-2026-07-LANE"


def test_quote_without_a_distance_on_a_per_mile_lane_is_422(seeded_client):
    """The rate exists; the request just cannot drive it."""
    response = seeded_client.get(
        f"{V1}/rates/quote",
        params={
            "carrier_scac": "CHRW", "mode": "ftl", "origin_state": "CA",
            "dest_state": "IL", "weight_lb": "30000", "service_level": "standard",
            "ship_date": "2026-08-11",
        },
    )
    assert response.status_code == 422
    assert "distance" in response.json()["detail"]


def test_quote_without_a_contract_is_404(seeded_client):
    response = seeded_client.get(
        f"{V1}/rates/quote",
        params={
            "carrier_scac": "RLCA", "mode": "ltl", "origin_state": "OH",
            "dest_state": "PA", "weight_lb": "1500", "ship_date": "2026-08-11",
        },
    )
    assert response.status_code == 404
    assert "No effective" in response.json()["detail"]


def test_create_rate(client):
    response = client.post(
        f"{V1}/rates",
        json={
            "carrier_scac": "abcd", "mode": "ltl", "charge_code": "freight",
            "rate_type": "per_cwt", "base_rate": "25.00", "effective_date": "2026-01-01",
        },
    )
    assert response.status_code == 201
    assert response.json()["carrier_scac"] == "ABCD"
    assert response.json()["charge_code"] == "FREIGHT"


# ---------------------------------------------------------------------------
# Audit workflow
# ---------------------------------------------------------------------------


def test_rule_catalogue_is_published(client):
    rules = client.get(f"{V1}/audits/rules").json()
    assert len(rules) == 7
    assert {"code", "name", "severity", "description", "monetary"} == set(rules[0])


def test_running_an_audit_returns_the_run_and_its_exceptions(seeded_client):
    response = seeded_client.post(f"{V1}/audits/run", json={"triggered_by": "pytest"})
    assert response.status_code == 201

    body = response.json()
    assert body["run"]["invoices_audited"] == 27
    assert body["run"]["triggered_by"] == "pytest"
    assert body["run"]["status"] == "completed"
    assert len(body["exceptions"]) == body["run"]["exceptions_found"]
    # Exceptions come back worst-dollar first.
    variances = [Decimal(exc["variance_amount"]) for exc in body["exceptions"]]
    assert variances == sorted(variances, reverse=True)


def test_exception_filters(seeded_client):
    seeded_client.post(f"{V1}/audits/run")

    duplicates = seeded_client.get(
        f"{V1}/audits/exceptions", params={"rule_code": "duplicate_invoice"}
    ).json()
    assert duplicates["total"] == 1

    critical = seeded_client.get(
        f"{V1}/audits/exceptions", params={"severity": "critical"}
    ).json()
    assert critical["total"] == 1

    odfl = seeded_client.get(
        f"{V1}/audits/exceptions", params={"carrier_scac": "odfl"}
    ).json()
    assert odfl["total"] > 0

    big = seeded_client.get(
        f"{V1}/audits/exceptions", params={"min_variance": 100}
    ).json()
    assert all(Decimal(exc["variance_amount"]) >= 100 for exc in big["items"])


def test_an_exception_can_be_worked_to_recovered(seeded_client, seeded_db):
    seeded_client.post(f"{V1}/audits/run")
    exception_id = seeded_db.query(AuditException).first().id

    response = seeded_client.patch(
        f"{V1}/audits/exceptions/{exception_id}",
        json={"status": "recovered", "resolution_note": "Credit memo CM-7781 received."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "recovered"
    assert "CM-7781" in response.json()["resolution_note"]


def test_updating_an_unknown_exception_is_404(seeded_client):
    assert seeded_client.patch(
        f"{V1}/audits/exceptions/424242", json={"status": "dismissed"}
    ).status_code == 404


def test_run_history_and_latest(seeded_client):
    assert seeded_client.get(f"{V1}/audits/runs/latest").status_code == 404

    seeded_client.post(f"{V1}/audits/run")
    seeded_client.post(f"{V1}/audits/run")

    runs = seeded_client.get(f"{V1}/audits/runs").json()
    assert len(runs) == 2
    assert seeded_client.get(f"{V1}/audits/runs/latest").json()["id"] == runs[0]["id"]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def test_summary_reconciles_billed_expected_and_variance(seeded_client, seeded_db):
    run = run_audit(seeded_db)
    body = seeded_client.get(f"{V1}/analytics/summary").json()

    assert body["shipments"] == 24
    assert body["invoices_audited"] == 27
    assert Decimal(body["total_billed"]) == run.total_billed
    assert Decimal(body["total_variance"]) == run.total_variance
    assert Decimal(body["total_billed"]) - Decimal(body["total_variance"]) == Decimal(
        body["total_expected"]
    )
    assert body["exceptions_detected"] == run.exceptions_found
    assert Decimal(body["recoverable_amount"]) > 0
    assert Decimal(body["recovered_amount"]) == 0
    # The per-rule breakdown is embedded, so one call drives the dashboard.
    assert {row["rule_code"] for row in body["by_rule"]} == {
        "RATE_MISMATCH", "DUPLICATE_INVOICE", "ACCESSORIAL_NOT_ORDERED",
        "UNCONTRACTED_CHARGE", "FUEL_SURCHARGE_VARIANCE", "SERVICE_FAILURE",
        "MISSING_SHIPMENT",
    }
    assert body["by_rule"][0]["rule_code"] == "RATE_MISMATCH"


def test_recovering_an_exception_moves_money_from_recoverable_to_recovered(
    seeded_client, seeded_db
):
    seeded_client.post(f"{V1}/audits/run")
    before = seeded_client.get(f"{V1}/analytics/summary").json()

    worst = (
        seeded_db.query(AuditException)
        .order_by(AuditException.variance_amount.desc())
        .first()
    )
    seeded_client.patch(f"{V1}/audits/exceptions/{worst.id}", json={"status": "recovered"})
    after = seeded_client.get(f"{V1}/analytics/summary").json()

    assert Decimal(after["recovered_amount"]) == worst.variance_amount
    assert Decimal(after["recoverable_amount"]) == Decimal(
        before["recoverable_amount"]
    ) - worst.variance_amount
    assert after["exceptions_detected"] == before["exceptions_detected"]


def test_carrier_scorecards_reconcile_with_the_summary(seeded_client):
    seeded_client.post(f"{V1}/audits/run")
    summary = seeded_client.get(f"{V1}/analytics/summary").json()
    cards = seeded_client.get(f"{V1}/analytics/carriers").json()

    # Every dollar and every exception belongs to exactly one carrier.
    assert sum(Decimal(card["total_billed"]) for card in cards) == Decimal(
        summary["total_billed"]
    )
    assert sum(Decimal(card["total_variance"]) for card in cards) == Decimal(
        summary["total_variance"]
    )
    assert sum(card["invoices"] for card in cards) == summary["invoices_audited"]
    assert sum(card["shipments"] for card in cards) == summary["shipments"]
    assert sum(card["exceptions"] for card in cards) == summary["exceptions_detected"]

    # Worst dollars first, so an analyst starts where the money is.
    variances = [Decimal(card["total_variance"]) for card in cards]
    assert variances == sorted(variances, reverse=True)
    assert cards[0]["carrier_scac"] == "XPOL"


def test_a_carrier_scorecard_scores_price_and_service_separately(seeded_client):
    seeded_client.post(f"{V1}/audits/run")
    cards = {
        card["carrier_scac"]: card
        for card in seeded_client.get(f"{V1}/analytics/carriers").json()
    }

    xpo = cards["XPOL"]
    assert xpo["carrier_name"] == "XPO Logistics"
    assert Decimal(xpo["variance_rate"]) == (
        Decimal(xpo["total_variance"]) / Decimal(xpo["total_billed"])
    ).quantize(Decimal("0.0001"))
    # Four of five XPO deliveries met the promised date.
    assert Decimal(xpo["on_time_rate"]) == Decimal("0.8000")

    # A carrier that billed cleanly still gets a card, with zeroes.
    assert cards["UPSN"]["exceptions"] == 0
    assert Decimal(cards["UPSN"]["total_variance"]) == 0
    assert Decimal(cards["UPSN"]["on_time_rate"]) == 1


def test_carrier_scorecards_respect_the_date_window(seeded_client):
    seeded_client.post(f"{V1}/audits/run")
    august = seeded_client.get(
        f"{V1}/analytics/carriers", params={"start": "2026-08-01", "end": "2026-08-31"}
    ).json()
    everything = seeded_client.get(f"{V1}/analytics/carriers").json()

    assert sum(card["invoices"] for card in august) == 12
    assert sum(card["invoices"] for card in august) < sum(
        card["invoices"] for card in everything
    )


def test_lane_spend_accounts_for_every_matched_invoice(seeded_client):
    seeded_client.post(f"{V1}/audits/run")
    summary = seeded_client.get(f"{V1}/analytics/summary").json()
    lanes = seeded_client.get(f"{V1}/analytics/lanes", params={"limit": 200}).json()
    unmatched = seeded_client.get(
        f"{V1}/invoices", params={"unmatched_only": True}
    ).json()

    # A lane comes from the shipment, so an unmatched invoice has none — those
    # dollars are the only ones missing from this view.
    orphaned = sum(Decimal(inv["total_amount"]) for inv in unmatched["items"])
    assert sum(Decimal(lane["total_billed"]) for lane in lanes) + orphaned == Decimal(
        summary["total_billed"]
    )
    assert sum(lane["invoices"] for lane in lanes) + unmatched["total"] == (
        summary["invoices_audited"]
    )

    billed = [Decimal(lane["total_billed"]) for lane in lanes]
    assert billed == sorted(billed, reverse=True)


def test_cost_per_cwt_is_priced_off_shipment_weight(seeded_client):
    seeded_client.post(f"{V1}/audits/run")
    lanes = {
        (lane["origin_state"], lane["dest_state"]): lane
        for lane in seeded_client.get(
            f"{V1}/analytics/lanes", params={"limit": 200}
        ).json()
    }

    ca_tx = lanes[("CA", "TX")]
    assert ca_tx["mode"] == "ltl"
    assert Decimal(ca_tx["cost_per_cwt"]) == (
        Decimal(ca_tx["total_billed"]) / (Decimal(ca_tx["total_weight_lb"]) / 100)
    ).quantize(Decimal("0.01"))

    # CO->NM carries one shipment billed twice. The duplicate must not
    # multiply the lane's weight, or cost per cwt halves and looks like a win.
    co_nm = lanes[("CO", "NM")]
    assert (co_nm["shipments"], co_nm["invoices"]) == (1, 2)
    assert Decimal(co_nm["total_weight_lb"]) == Decimal("1250.00")


def test_lane_limit_returns_the_biggest_spend_first(seeded_client):
    top = seeded_client.get(f"{V1}/analytics/lanes", params={"limit": 3}).json()
    everything = seeded_client.get(
        f"{V1}/analytics/lanes", params={"limit": 200}
    ).json()

    assert len(top) == 3
    assert len(everything) > 3
    assert [lane["origin_state"] for lane in top] == [
        lane["origin_state"] for lane in everything[:3]
    ]


def test_analytics_respects_a_date_window(seeded_client):
    august = seeded_client.get(
        f"{V1}/analytics/summary",
        params={"start": "2026-08-01", "end": "2026-08-31"},
    ).json()
    assert august["invoices_audited"] == 12


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def test_upload_a_shipment_csv(client):
    csv_body = (
        "shipment_id,carrier_scac,mode,origin_state,origin_zip,dest_state,dest_zip,ship_date,weight_lb\n"
        "SHP-UP1,ODFL,ltl,CA,90021,TX,75201,2026-03-02,1000\n"
    )
    response = client.post(
        f"{V1}/ingest/shipments",
        files={"file": ("shipments.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["created"], body["updated"], body["errors"]) == (1, 0, [])
    assert client.get(f"{V1}/shipments/SHP-UP1").status_code == 200


def test_upload_reports_bad_rows_without_failing_the_request(client):
    csv_body = (
        "shipment_id,carrier_scac,mode,origin_state,origin_zip,dest_state,dest_zip,ship_date,weight_lb\n"
        "SHP-UP2,ODFL,ltl,CA,90021,TX,75201,2026-03-02,1000\n"
        "SHP-UP3,ODFL,hovercraft,CA,90021,TX,75201,2026-03-02,1000\n"
    )
    body = client.post(
        f"{V1}/ingest/shipments", files={"file": ("s.csv", csv_body, "text/csv")}
    ).json()

    assert body["created"] == 1
    assert body["errors"][0]["row"] == 3
    assert "invalid mode" in body["errors"][0]["message"]


def test_upload_rejects_an_unknown_dataset_and_an_empty_file(client):
    assert client.post(
        f"{V1}/ingest/freight_bills", files={"file": ("x.csv", "a,b\n1,2\n", "text/csv")}
    ).status_code == 400
    assert client.post(
        f"{V1}/ingest/shipments", files={"file": ("x.csv", "", "text/csv")}
    ).status_code == 400


def test_loading_the_bundled_sample_data_through_the_api(client):
    body = client.post(f"{V1}/ingest/sample-data").json()
    assert set(body) == {"shipments", "carrier_invoices", "contracted_rates"}
    assert body["shipments"]["created"] == 24
    assert client.get(f"{V1}/shipments", params={"limit": 1}).json()["total"] == 24
