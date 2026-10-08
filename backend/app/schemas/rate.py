"""Contracted-rate request/response models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field, field_validator

from app.models.enums import RateType, TransportMode
from app.schemas.common import ORMModel


class ContractedRateBase(ORMModel):
    carrier_scac: str = Field(max_length=8)
    mode: TransportMode
    service_level: str = "*"
    origin_state: str = "*"
    dest_state: str = "*"
    charge_code: str
    rate_type: RateType
    base_rate: Decimal = Decimal("0")
    min_weight_lb: Decimal = Decimal("0")
    max_weight_lb: Decimal | None = None
    min_charge: Decimal | None = None
    max_charge: Decimal | None = None
    discount_pct: Decimal = Decimal("0")
    fuel_surcharge_pct: Decimal = Decimal("0")
    effective_date: date
    expiration_date: date | None = None
    contract_id: str | None = None

    @field_validator("carrier_scac", "origin_state", "dest_state", "charge_code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class ContractedRateCreate(ContractedRateBase):
    pass


class ContractedRateRead(ContractedRateBase):
    id: int
