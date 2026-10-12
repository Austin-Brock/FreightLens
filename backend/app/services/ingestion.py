"""CSV / feed ingestion for shipments, carrier invoices and contracted rates.

Real freight data arrives dirty: inconsistent header casing, blank numerics,
dates in three formats, carrier-specific accessorial names. Everything in this
module exists to normalise that into the canonical model without dropping a row
silently — anything unusable is returned as an :class:`IngestionError` with its
row number so the operator can fix the source file.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    WILDCARD,
    CarrierInvoice,
    ChargeCode,
    ContractedRate,
    InvoiceLineItem,
    InvoiceStatus,
    RateType,
    Shipment,
    TransportMode,
)
from app.schemas.ingestion import IngestionError, IngestionResult

logger = logging.getLogger(__name__)

DATASETS = ("shipments", "contracted_rates", "carrier_invoices")

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y%m%d")

#: Carrier descriptions and legacy codes mapped onto canonical charge codes.
CHARGE_CODE_ALIASES: dict[str, str] = {
    "FRT": ChargeCode.FREIGHT.value,
    "LINEHAUL": ChargeCode.FREIGHT.value,
    "BASE": ChargeCode.FREIGHT.value,
    "FSC": ChargeCode.FUEL.value,
    "FUEL_SURCHARGE": ChargeCode.FUEL.value,
    "LFTG": ChargeCode.LIFTGATE.value,
    "LGATE": ChargeCode.LIFTGATE.value,
    "RESI": ChargeCode.RESIDENTIAL.value,
    "RES": ChargeCode.RESIDENTIAL.value,
    "INSD": ChargeCode.INSIDE_DELIVERY.value,
    "IDEL": ChargeCode.INSIDE_DELIVERY.value,
    "DETN": ChargeCode.DETENTION.value,
    "DET": ChargeCode.DETENTION.value,
    "REWT": ChargeCode.REWEIGH.value,
    "LACC": ChargeCode.LIMITED_ACCESS.value,
    "APPT": ChargeCode.APPOINTMENT.value,
    "ADRC": ChargeCode.ADDRESS_CORRECTION.value,
    "OVSZ": ChargeCode.OVERSIZE.value,
    "DVAL": ChargeCode.DECLARED_VALUE.value,
}

_KNOWN_CHARGE_CODES = {code.value for code in ChargeCode}


class RowError(ValueError):
    """Raised by a normaliser when a row cannot be used."""


# ---------------------------------------------------------------------------
# Scalar normalisation
# ---------------------------------------------------------------------------


def norm_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")


def as_str(row: Mapping[str, str], *keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def required_str(row: Mapping[str, str], *keys: str) -> str:
    value = as_str(row, *keys)
    if value is None:
        raise RowError(f"missing required column: {' | '.join(keys)}")
    return value


def as_decimal(
    row: Mapping[str, str], *keys: str, default: Decimal | None = None
) -> Decimal | None:
    raw = as_str(row, *keys)
    if raw is None:
        return default
    cleaned = raw.replace(",", "").replace("$", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    if cleaned.endswith("%"):
        try:
            return Decimal(cleaned[:-1]) / Decimal("100")
        except InvalidOperation as exc:
            raise RowError(f"cannot parse percentage {raw!r}") from exc
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise RowError(f"cannot parse number {raw!r}") from exc
    return -value if negative else value


def as_int(row: Mapping[str, str], *keys: str, default: int | None = None) -> int | None:
    value = as_decimal(row, *keys)
    return int(value) if value is not None else default


def as_date(row: Mapping[str, str], *keys: str, required: bool = False) -> date | None:
    raw = as_str(row, *keys)
    if raw is None:
        if required:
            raise RowError(f"missing required date: {' | '.join(keys)}")
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise RowError(f"unrecognised date format {raw!r}")


def as_enum(value: str, enum_cls, field_name: str):
    candidate = value.strip().lower().replace(" ", "_")
    try:
        return enum_cls(candidate)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise RowError(f"invalid {field_name} {value!r}; expected one of: {allowed}") from exc


def canonical_charge_code(raw: str | None) -> str:
    """Map a carrier's charge label onto a canonical code."""
    if not raw:
        return ChargeCode.OTHER.value
    token = raw.strip().upper().replace(" ", "_").replace("-", "_")
    if token in _KNOWN_CHARGE_CODES:
        return token
    return CHARGE_CODE_ALIASES.get(token, ChargeCode.OTHER.value)


def normalize_accessorials(raw: str | None) -> str | None:
    """Normalise ``"liftgate; resi"`` into ``"LIFTGATE|RESIDENTIAL"``."""
    if not raw or not raw.strip():
        return None
    parts: list[str] = []
    for chunk in raw.replace(";", "|").replace(",", "|").split("|"):
        if not chunk.strip():
            continue
        code = canonical_charge_code(chunk)
        if code not in parts:
            parts.append(code)
    return "|".join(parts) or None


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------

CsvSource = str | bytes | Path | io.IOBase


def read_rows(source: CsvSource) -> list[dict[str, str]]:
    """Read a CSV from a path, raw text/bytes, or an open file object."""
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8-sig")
    elif isinstance(source, bytes):
        text = source.decode("utf-8-sig")
    elif isinstance(source, str):
        # A single-line string with no comma is treated as a path.
        text = (
            Path(source).read_text(encoding="utf-8-sig")
            if "\n" not in source and "," not in source
            else source
        )
    else:
        raw = source.read()
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return []
    reader.fieldnames = [norm_header(name) for name in reader.fieldnames]
    return [
        {key: (value or "") for key, value in row.items() if key}
        for row in reader
        if any((value or "").strip() for value in row.values())
    ]


# ---------------------------------------------------------------------------
# Shipments
# ---------------------------------------------------------------------------


def _shipment_fields(row: Mapping[str, str]) -> dict:
    return {
        "shipment_id": required_str(row, "shipment_id", "shipment", "pro_number"),
        "reference_number": as_str(row, "reference_number", "reference", "bol"),
        "carrier_scac": required_str(row, "carrier_scac", "scac", "carrier").upper(),
        "carrier_name": as_str(row, "carrier_name", "carrier"),
        "mode": as_enum(required_str(row, "mode"), TransportMode, "mode"),
        "service_level": (as_str(row, "service_level", "service", default="standard") or "standard")
        .strip()
        .lower(),
        "origin_city": as_str(row, "origin_city"),
        "origin_state": required_str(row, "origin_state").upper(),
        "origin_zip": required_str(row, "origin_zip"),
        "dest_city": as_str(row, "dest_city", "destination_city"),
        "dest_state": required_str(row, "dest_state", "destination_state").upper(),
        "dest_zip": required_str(row, "dest_zip", "destination_zip"),
        "distance_mi": as_decimal(row, "distance_mi", "miles", "distance"),
        "ship_date": as_date(row, "ship_date", "pickup_date", required=True),
        "promised_date": as_date(row, "promised_date", "committed_date"),
        "delivery_date": as_date(row, "delivery_date", "actual_delivery_date"),
        "weight_lb": as_decimal(row, "weight_lb", "weight", default=Decimal("0")),
        "billable_weight_lb": as_decimal(row, "billable_weight_lb", "billable_weight"),
        "pieces": as_int(row, "pieces", "piece_count", default=1),
        "freight_class": as_str(row, "freight_class", "class"),
        "declared_value": as_decimal(row, "declared_value"),
        "accessorials": normalize_accessorials(
            as_str(row, "accessorials", "accessorial_codes", "services")
        ),
        "customer": as_str(row, "customer", "customer_name"),
        "cost_center": as_str(row, "cost_center", "gl_code"),
    }


def ingest_shipments(db: Session, rows: Iterable[Mapping[str, str]]) -> IngestionResult:
    result = IngestionResult(dataset="shipments")
    existing = {s.shipment_id: s for s in db.scalars(select(Shipment)).all()}

    for offset, row in enumerate(rows, start=2):  # row 1 is the header
        result.rows_read += 1
        try:
            fields = _shipment_fields(row)
        except RowError as exc:
            result.errors.append(IngestionError(row=offset, message=str(exc), data=dict(row)))
            continue

        shipment = existing.get(fields["shipment_id"])
        if shipment is None:
            shipment = Shipment(**fields)
            db.add(shipment)
            existing[fields["shipment_id"]] = shipment
            result.created += 1
        else:
            for key, value in fields.items():
                setattr(shipment, key, value)
            result.updated += 1

    db.flush()
    return result


# ---------------------------------------------------------------------------
# Contracted rates
# ---------------------------------------------------------------------------


def _rate_fields(row: Mapping[str, str]) -> dict:
    return {
        "carrier_scac": required_str(row, "carrier_scac", "scac").upper(),
        "mode": as_enum(required_str(row, "mode"), TransportMode, "mode"),
        "service_level": (as_str(row, "service_level", "service", default=WILDCARD) or WILDCARD)
        .strip()
        .lower(),
        "origin_state": (as_str(row, "origin_state", default=WILDCARD) or WILDCARD).upper(),
        "dest_state": (
            as_str(row, "dest_state", "destination_state", default=WILDCARD) or WILDCARD
        ).upper(),
        "charge_code": canonical_charge_code(required_str(row, "charge_code", "charge")),
        "rate_type": as_enum(required_str(row, "rate_type"), RateType, "rate_type"),
        "base_rate": as_decimal(row, "base_rate", "rate", default=Decimal("0")),
        "min_weight_lb": as_decimal(row, "min_weight_lb", "weight_break_min", default=Decimal("0")),
        "max_weight_lb": as_decimal(row, "max_weight_lb", "weight_break_max"),
        "min_charge": as_decimal(row, "min_charge", "minimum_charge"),
        "max_charge": as_decimal(row, "max_charge", "maximum_charge"),
        "discount_pct": as_decimal(row, "discount_pct", "discount", default=Decimal("0")),
        "fuel_surcharge_pct": as_decimal(
            row, "fuel_surcharge_pct", "fsc_pct", default=Decimal("0")
        ),
        "effective_date": as_date(row, "effective_date", required=True),
        "expiration_date": as_date(row, "expiration_date", "expiry_date"),
        "contract_id": as_str(row, "contract_id", "agreement_id"),
    }


def _rate_key(fields: Mapping) -> tuple:
    return (
        fields["carrier_scac"],
        str(fields["mode"]),
        fields["service_level"],
        fields["origin_state"],
        fields["dest_state"],
        fields["charge_code"],
        Decimal(fields["min_weight_lb"]),
        fields["effective_date"],
    )


def ingest_contracted_rates(
    db: Session, rows: Iterable[Mapping[str, str]]
) -> IngestionResult:
    result = IngestionResult(dataset="contracted_rates")
    existing = {
        _rate_key(
            {
                "carrier_scac": r.carrier_scac,
                "mode": r.mode,
                "service_level": r.service_level,
                "origin_state": r.origin_state,
                "dest_state": r.dest_state,
                "charge_code": r.charge_code,
                "min_weight_lb": r.min_weight_lb,
                "effective_date": r.effective_date,
            }
        ): r
        for r in db.scalars(select(ContractedRate)).all()
    }

    for offset, row in enumerate(rows, start=2):
        result.rows_read += 1
        try:
            fields = _rate_fields(row)
        except RowError as exc:
            result.errors.append(IngestionError(row=offset, message=str(exc), data=dict(row)))
            continue

        key = _rate_key(fields)
        rate_row = existing.get(key)
        if rate_row is None:
            rate_row = ContractedRate(**fields)
            db.add(rate_row)
            existing[key] = rate_row
            result.created += 1
        else:
            for name, value in fields.items():
                setattr(rate_row, name, value)
            result.updated += 1

    db.flush()
    return result


# ---------------------------------------------------------------------------
# Carrier invoices
# ---------------------------------------------------------------------------


def _group_invoice_rows(
    rows: Iterable[Mapping[str, str]]
) -> Iterator[tuple[tuple[str, str], list[tuple[int, Mapping[str, str]]]]]:
    """Group flat invoice-line rows into (carrier, invoice_number) buckets."""
    grouped: dict[tuple[str, str], list[tuple[int, Mapping[str, str]]]] = {}
    for offset, row in enumerate(rows, start=2):
        carrier = (as_str(row, "carrier_scac", "scac") or "").upper()
        number = as_str(row, "invoice_number", "invoice", "invoice_no") or ""
        grouped.setdefault((carrier, number), []).append((offset, row))
    yield from grouped.items()


def _invoice_header_fields(row: Mapping[str, str]) -> dict:
    return {
        "invoice_number": required_str(row, "invoice_number", "invoice", "invoice_no"),
        "carrier_scac": required_str(row, "carrier_scac", "scac").upper(),
        "shipment_ref": as_str(row, "shipment_id", "shipment_ref", "pro_number", "reference"),
        "invoice_date": as_date(row, "invoice_date", required=True),
        "due_date": as_date(row, "due_date"),
        "currency": (as_str(row, "currency", default="USD") or "USD").upper(),
    }


def _line_item_fields(row: Mapping[str, str], line_number: int) -> dict:
    return {
        "line_number": as_int(row, "line_number", default=line_number) or line_number,
        "charge_code": canonical_charge_code(
            as_str(row, "charge_code", "charge", "charge_description")
        ),
        "description": as_str(row, "charge_description", "description"),
        "quantity": as_decimal(row, "quantity", "qty", default=Decimal("1")),
        "unit": as_str(row, "unit", "uom"),
        "amount": as_decimal(row, "amount", "charge_amount", default=Decimal("0")),
    }


def ingest_carrier_invoices(
    db: Session, rows: Iterable[Mapping[str, str]]
) -> IngestionResult:
    result = IngestionResult(dataset="carrier_invoices")
    shipments = {s.shipment_id.upper(): s for s in db.scalars(select(Shipment)).all()}
    existing = {
        (inv.carrier_scac, inv.invoice_number): inv
        for inv in db.scalars(select(CarrierInvoice)).all()
    }

    for (_carrier, _number), group in _group_invoice_rows(rows):
        result.rows_read += len(group)
        first_offset, first_row = group[0]
        try:
            header = _invoice_header_fields(first_row)
            lines = [
                _line_item_fields(row, index)
                for index, (_offset, row) in enumerate(group, start=1)
                # A header-only row (no charge code and no amount) carries the
                # invoice total but no charge of its own.
                if as_str(row, "charge_code", "charge", "amount") is not None
            ]
            stated_total = as_decimal(first_row, "invoice_total", "total_amount", "total")
        except RowError as exc:
            result.errors.append(
                IngestionError(row=first_offset, message=str(exc), data=dict(first_row))
            )
            continue

        line_sum = sum((line["amount"] for line in lines), Decimal("0"))
        header["total_amount"] = stated_total if stated_total is not None else line_sum

        matched = shipments.get((header["shipment_ref"] or "").upper())
        key = (header["carrier_scac"], header["invoice_number"])
        invoice = existing.get(key)
        if invoice is None:
            invoice = CarrierInvoice(**header, status=InvoiceStatus.RECEIVED)
            db.add(invoice)
            existing[key] = invoice
            result.created += 1
        else:
            for name, value in header.items():
                setattr(invoice, name, value)
            invoice.line_items.clear()
            result.updated += 1

        invoice.shipment_id = matched.id if matched else None
        invoice.shipment = matched
        for line in lines:
            invoice.line_items.append(InvoiceLineItem(**line))

    db.flush()
    return result


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------

_INGESTORS = {
    "shipments": ingest_shipments,
    "contracted_rates": ingest_contracted_rates,
    "carrier_invoices": ingest_carrier_invoices,
}


def ingest_dataset(
    db: Session, dataset: str, source: CsvSource, *, commit: bool = True
) -> IngestionResult:
    """Ingest one named dataset from a CSV source."""
    if dataset not in _INGESTORS:
        raise ValueError(f"unknown dataset {dataset!r}; expected one of {', '.join(DATASETS)}")
    result = _INGESTORS[dataset](db, read_rows(source))
    if commit:
        db.commit()
    logger.info(
        "Ingested %s: %d created, %d updated, %d error(s)",
        dataset,
        result.created,
        result.updated,
        len(result.errors),
    )
    return result


def relink_invoices(db: Session) -> int:
    """Attach invoices whose shipment arrived after they did. Returns count linked."""
    shipments = {s.shipment_id.upper(): s.id for s in db.scalars(select(Shipment)).all()}
    linked = 0
    orphans = db.scalars(
        select(CarrierInvoice).where(CarrierInvoice.shipment_id.is_(None))
    ).all()
    for invoice in orphans:
        shipment_id = shipments.get((invoice.shipment_ref or "").upper())
        if shipment_id is not None:
            invoice.shipment_id = shipment_id
            linked += 1
    if linked:
        db.commit()
    return linked


def ingest_directory(
    db: Session, directory: Path, *, datasets: Sequence[str] = DATASETS
) -> dict[str, IngestionResult]:
    """Ingest ``<directory>/<dataset>.csv`` for each dataset, in dependency order."""
    results: dict[str, IngestionResult] = {}
    for dataset in datasets:
        path = Path(directory) / f"{dataset}.csv"
        if not path.exists():
            logger.warning("Skipping %s: %s not found", dataset, path)
            continue
        results[dataset] = ingest_dataset(db, dataset, path, commit=False)
    db.commit()
    relink_invoices(db)
    return results


__all__ = [
    "DATASETS",
    "IngestionResult",
    "canonical_charge_code",
    "ingest_carrier_invoices",
    "ingest_contracted_rates",
    "ingest_dataset",
    "ingest_directory",
    "ingest_shipments",
    "normalize_accessorials",
    "read_rows",
    "relink_invoices",
]
