"""Shipment request/response models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field, field_validator

from app.models.enums import TransportMode
from app.schemas.common import ORMModel


class ShipmentBase(ORMModel):
    shipment_id: str = Field(max_length=64)
    reference_number: str | None = None
    carrier_scac: str = Field(max_length=8)
    carrier_name: str | None = None
    mode: TransportMode
    service_level: str = "standard"

    origin_city: str | None = None
    origin_state: str = Field(min_length=2, max_length=2)
    origin_zip: str
    dest_city: str | None = None
    dest_state: str = Field(min_length=2, max_length=2)
    dest_zip: str
    distance_mi: Decimal | None = None

    ship_date: date
    promised_date: date | None = None
    delivery_date: date | None = None

    weight_lb: Decimal = Decimal("0")
    billable_weight_lb: Decimal | None = None
    pieces: int = 1
    freight_class: str | None = None
    declared_value: Decimal | None = None
    accessorials: str | None = None
    customer: str | None = None
    cost_center: str | None = None

    @field_validator("carrier_scac", "origin_state", "dest_state")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class ShipmentCreate(ShipmentBase):
    pass


class ShipmentRead(ShipmentBase):
    id: int
    transit_days: int | None = None
    is_late: bool = False
