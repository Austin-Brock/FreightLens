"""Shipment of record — the shipper's own view of what moved."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TransportMode


class Shipment(TimestampMixin, Base):
    """A single freight movement as recorded in the TMS/ERP.

    This is the source of truth the carrier's invoice gets audited against:
    if the carrier bills a liftgate that is not in ``accessorials`` here, or a
    weight that does not match ``weight_lb``, that is a recoverable variance.
    """

    __tablename__ = "shipments"
    __table_args__ = (
        Index("ix_shipments_lane", "origin_state", "dest_state"),
        Index("ix_shipments_carrier_date", "carrier_scac", "ship_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reference_number: Mapped[str | None] = mapped_column(String(64))

    carrier_scac: Mapped[str] = mapped_column(String(8), index=True)
    carrier_name: Mapped[str | None] = mapped_column(String(128))
    mode: Mapped[TransportMode] = mapped_column(String(16), index=True)
    service_level: Mapped[str] = mapped_column(String(32), default="standard")

    origin_city: Mapped[str | None] = mapped_column(String(64))
    origin_state: Mapped[str] = mapped_column(String(2))
    origin_zip: Mapped[str] = mapped_column(String(10))
    dest_city: Mapped[str | None] = mapped_column(String(64))
    dest_state: Mapped[str] = mapped_column(String(2))
    dest_zip: Mapped[str] = mapped_column(String(10))
    distance_mi: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))

    ship_date: Mapped[date] = mapped_column(Date, index=True)
    promised_date: Mapped[date | None] = mapped_column(Date)
    delivery_date: Mapped[date | None] = mapped_column(Date)

    weight_lb: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    billable_weight_lb: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    pieces: Mapped[int] = mapped_column(default=1)
    freight_class: Mapped[str | None] = mapped_column(String(8))
    declared_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Pipe-delimited canonical charge codes the shipper actually ordered,
    # e.g. "LIFTGATE|RESIDENTIAL". Stored denormalised because it is only ever
    # read as a set membership test during the audit.
    accessorials: Mapped[str | None] = mapped_column(String(256))

    customer: Mapped[str | None] = mapped_column(String(128))
    cost_center: Mapped[str | None] = mapped_column(String(64))

    invoices: Mapped[list[CarrierInvoice]] = relationship(  # noqa: F821
        back_populates="shipment", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list[AuditException]] = relationship(  # noqa: F821
        back_populates="shipment", cascade="all, delete-orphan"
    )

    @property
    def ordered_accessorials(self) -> set[str]:
        if not self.accessorials:
            return set()
        return {part.strip().upper() for part in self.accessorials.split("|") if part.strip()}

    @property
    def rated_weight_lb(self) -> Decimal:
        """Weight the contract should be applied to."""
        return self.billable_weight_lb or self.weight_lb

    @property
    def transit_days(self) -> int | None:
        if self.delivery_date is None:
            return None
        return (self.delivery_date - self.ship_date).days

    @property
    def is_late(self) -> bool:
        if self.promised_date is None or self.delivery_date is None:
            return False
        return self.delivery_date > self.promised_date

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Shipment {self.shipment_id} {self.carrier_scac} "
            f"{self.origin_state}->{self.dest_state}>"
        )
