"""Ingestion: normalisation, upsert behaviour and error reporting."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import CarrierInvoice, ContractedRate, Shipment
from app.services import ingestion
from app.services.ingestion import (
    as_date,
    as_decimal,
    canonical_charge_code,
    normalize_accessorials,
    read_rows,
)

SHIPMENT_CSV = """Shipment ID,Carrier SCAC,Mode,Origin State,Origin Zip,Dest State,Dest Zip,Ship Date,Weight,Accessorials
SHP-A,odfl,LTL,ca,90021,tx,75201,2026-03-02,"1,250",liftgate; resi
SHP-B,XPOL,ltl,IL,60607,GA,30336,03/04/2026,800,
"""


def test_read_rows_normalises_headers_and_skips_blank_lines():
    rows = read_rows(SHIPMENT_CSV + "\n,,,,,,,,,\n")
    assert len(rows) == 2
    assert set(rows[0]) >= {"shipment_id", "carrier_scac", "origin_state", "ship_date"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,250.50", Decimal("1250.50")),
        ("$95.00", Decimal("95")),
        ("(40.00)", Decimal("-40")),
        ("22%", Decimal("0.22")),
        ("", None),
    ],
)
def test_as_decimal_handles_freight_formatting(raw, expected):
    assert as_decimal({"x": raw}, "x") == expected


def test_as_decimal_rejects_garbage():
    with pytest.raises(ingestion.RowError):
        as_decimal({"x": "n/a"}, "x")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-02", date(2026, 3, 2)),
        ("03/02/2026", date(2026, 3, 2)),
        ("20260302", date(2026, 3, 2)),
        ("02-Mar-2026", date(2026, 3, 2)),
    ],
)
def test_as_date_accepts_common_freight_formats(raw, expected):
    assert as_date({"d": raw}, "d") == expected


def test_as_date_rejects_unknown_format():
    with pytest.raises(ingestion.RowError):
        as_date({"d": "March 2nd"}, "d")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("LFTG", "LIFTGATE"), ("fsc", "FUEL"), ("linehaul", "FREIGHT"), ("mystery", "OTHER")],
)
def test_canonical_charge_code_maps_carrier_aliases(raw, expected):
    assert canonical_charge_code(raw) == expected


def test_normalize_accessorials_dedupes_and_canonicalises():
    assert normalize_accessorials("liftgate; resi, LFTG") == "LIFTGATE|RESIDENTIAL"
    assert normalize_accessorials("  ") is None


def test_ingest_shipments_creates_then_updates(db):
    first = ingestion.ingest_shipments(db, read_rows(SHIPMENT_CSV))
    assert (first.created, first.updated, first.errors) == (2, 0, [])

    shipment = db.query(Shipment).filter_by(shipment_id="SHP-A").one()
    assert shipment.carrier_scac == "ODFL"
    assert shipment.weight_lb == Decimal("1250")
    assert shipment.ordered_accessorials == {"LIFTGATE", "RESIDENTIAL"}

    again = ingestion.ingest_shipments(db, read_rows(SHIPMENT_CSV))
    assert (again.created, again.updated) == (0, 2)
    assert db.query(Shipment).count() == 2


def test_ingest_shipments_reports_bad_row_and_keeps_the_rest(db):
    broken = SHIPMENT_CSV + "SHP-C,ODFL,ltl,OH,44115,PA,15222,not-a-date,500,\n"
    result = ingestion.ingest_shipments(db, read_rows(broken))

    assert result.created == 2
    assert len(result.errors) == 1
    assert result.errors[0].row == 4
    assert "date" in result.errors[0].message
    assert db.query(Shipment).filter_by(shipment_id="SHP-C").one_or_none() is None


def test_ingest_shipments_requires_a_shipment_id(db):
    result = ingestion.ingest_shipments(
        db, read_rows("mode,origin_state\nltl,CA\n")
    )
    assert result.created == 0
    assert "shipment_id" in result.errors[0].message


INVOICE_CSV = """invoice_number,carrier_scac,shipment_id,invoice_date,invoice_total,charge_code,charge_description,quantity,unit,amount
INV-9,ODFL,SHP-A,2026-03-10,480.00,FRT,Linehaul,1250,lb,356.25
INV-9,ODFL,SHP-A,2026-03-10,480.00,FSC,Fuel,1,pct,78.38
INV-9,ODFL,SHP-A,2026-03-10,480.00,LFTG,Liftgate,1,each,45.37
INV-10,XPOL,SHP-B,2026-03-11,,FRT,Linehaul,800,lb,228.00
"""


def test_ingest_invoices_groups_lines_and_links_shipments(db):
    ingestion.ingest_shipments(db, read_rows(SHIPMENT_CSV))
    result = ingestion.ingest_carrier_invoices(db, read_rows(INVOICE_CSV))

    assert (result.created, result.rows_read) == (2, 4)
    invoice = db.query(CarrierInvoice).filter_by(invoice_number="INV-9").one()
    assert len(invoice.line_items) == 3
    assert [line.charge_code for line in invoice.line_items] == ["FREIGHT", "FUEL", "LIFTGATE"]
    assert invoice.total_amount == Decimal("480.00")
    assert invoice.shipment.shipment_id == "SHP-A"


def test_ingest_invoices_falls_back_to_the_line_total(db):
    ingestion.ingest_shipments(db, read_rows(SHIPMENT_CSV))
    ingestion.ingest_carrier_invoices(db, read_rows(INVOICE_CSV))
    invoice = db.query(CarrierInvoice).filter_by(invoice_number="INV-10").one()
    assert invoice.total_amount == Decimal("228.00")


def test_reingesting_an_invoice_replaces_its_lines(db):
    ingestion.ingest_shipments(db, read_rows(SHIPMENT_CSV))
    ingestion.ingest_carrier_invoices(db, read_rows(INVOICE_CSV))
    result = ingestion.ingest_carrier_invoices(db, read_rows(INVOICE_CSV))

    assert (result.created, result.updated) == (0, 2)
    invoice = db.query(CarrierInvoice).filter_by(invoice_number="INV-9").one()
    assert len(invoice.line_items) == 3


def test_relink_invoices_adopts_late_arriving_shipments(db):
    ingestion.ingest_carrier_invoices(db, read_rows(INVOICE_CSV))
    assert db.query(CarrierInvoice).filter_by(invoice_number="INV-9").one().shipment_id is None

    ingestion.ingest_shipments(db, read_rows(SHIPMENT_CSV))
    assert ingestion.relink_invoices(db) == 2
    assert db.query(CarrierInvoice).filter_by(invoice_number="INV-9").one().shipment_id is not None


RATE_CSV = """carrier_scac,mode,service_level,origin_state,dest_state,charge_code,rate_type,base_rate,min_weight_lb,max_weight_lb,min_charge,discount_pct,effective_date,expiration_date
ODFL,ltl,standard,*,*,FREIGHT,per_cwt,28.50,500,1999,165.00,0,2026-01-01,2026-12-31
ODFL,ltl,*,*,*,LFTG,flat,95.00,0,,,0,2026-01-01,
"""


def test_ingest_rates_canonicalises_charge_codes_and_upserts(db):
    result = ingestion.ingest_contracted_rates(db, read_rows(RATE_CSV))
    assert (result.created, result.errors) == (2, [])

    liftgate = db.query(ContractedRate).filter_by(charge_code="LIFTGATE").one()
    assert liftgate.base_rate == Decimal("95.0000")
    assert liftgate.expiration_date is None

    again = ingestion.ingest_contracted_rates(db, read_rows(RATE_CSV))
    assert (again.created, again.updated) == (0, 2)


def test_ingest_dataset_rejects_an_unknown_dataset(db):
    with pytest.raises(ValueError, match="unknown dataset"):
        ingestion.ingest_dataset(db, "freight_bills", RATE_CSV)


def test_ingest_directory_loads_the_bundled_sample_data(db, sample_data_dir):
    results = ingestion.ingest_directory(db, sample_data_dir)

    assert set(results) == set(ingestion.DATASETS)
    assert all(not result.errors for result in results.values())
    assert db.query(Shipment).count() == results["shipments"].created
    # Every invoice but the deliberately unmatchable one links to a shipment.
    unmatched = db.query(CarrierInvoice).filter(CarrierInvoice.shipment_id.is_(None)).all()
    assert [invoice.shipment_ref for invoice in unmatched] == ["SHP-19999"]
