"""Contracted rates — the negotiated tariff the audit prices against."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import RateType, TransportMode

WILDCARD = "*"


class ContractedRate(TimestampMixin, Base):
    """One priced row of a carrier agreement.

    A single table covers both linehaul and accessorials: ``charge_code``
    decides which. ``"*"`` in a lane or service column means "any", so a
    national accessorial is one row instead of 2,500 lane permutations.
    """

    __tablename__ = "contracted_rates"
    __table_args__ = (
        UniqueConstraint(
            "carrier_scac",
            "mode",
            "service_level",
            "origin_state",
            "dest_state",
            "charge_code",
            "min_weight_lb",
            "effective_date",
            name="uq_contracted_rate_key",
        ),
        Index("ix_rates_lookup", "carrier_scac", "mode", "charge_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_scac: Mapped[str] = mapped_column(String(8), index=True)
    mode: Mapped[TransportMode] = mapped_column(String(16))
    service_level: Mapped[str] = mapped_column(String(32), default=WILDCARD)
    origin_state: Mapped[str] = mapped_column(String(2), default=WILDCARD)
    dest_state: Mapped[str] = mapped_column(String(2), default=WILDCARD)

    charge_code: Mapped[str] = mapped_column(String(32), index=True)
    rate_type: Mapped[RateType] = mapped_column(String(24))
    base_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))

    min_weight_lb: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    max_weight_lb: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    min_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Stored as a fraction (0.0825 == 8.25%).
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    fuel_surcharge_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))

    effective_date: Mapped[date] = mapped_column(Date)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    contract_id: Mapped[str | None] = mapped_column(String(64))

    def covers_date(self, when: date) -> bool:
        if when < self.effective_date:
            return False
        return self.expiration_date is None or when <= self.expiration_date

    def covers_weight(self, weight_lb: Decimal) -> bool:
        if weight_lb < self.min_weight_lb:
            return False
        return self.max_weight_lb is None or weight_lb <= self.max_weight_lb

    @property
    def specificity(self) -> int:
        """Higher wins when several rows match the same shipment."""
        score = 0
        for value in (self.service_level, self.origin_state, self.dest_state):
            if value and value != WILDCARD:
                score += 1
        if self.max_weight_lb is not None:
            score += 1
        return score

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ContractedRate {self.carrier_scac} {self.charge_code} "
            f"{self.origin_state}->{self.dest_state} {self.rate_type}:{self.base_rate}>"
        )
