"""Domain enumerations shared by models and schemas."""

from __future__ import annotations

from enum import Enum

try:  # Python 3.11+
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10 and earlier

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        """``str``-valued enum whose ``str()`` is the value, not ``Class.MEMBER``.

        Without this, ``str(TransportMode.LTL)`` yields ``"TransportMode.LTL"``
        on Python < 3.12, which silently breaks every dict key and comparison
        built from a mode or charge code.
        """

        def __str__(self) -> str:
            return str(self.value)


class TransportMode(StrEnum):
    PARCEL = "parcel"
    LTL = "ltl"
    FTL = "ftl"
    AIR = "air"
    OCEAN = "ocean"
    INTERMODAL = "intermodal"


class RateType(StrEnum):
    """How a contracted rate turns shipment attributes into money."""

    PER_CWT = "per_cwt"  # base_rate per 100 lb of billable weight
    PER_LB = "per_lb"
    PER_MILE = "per_mile"
    FLAT = "flat"
    PCT_OF_LINEHAUL = "pct_of_linehaul"  # fuel surcharge and similar


class ChargeCode(StrEnum):
    """Canonical charge codes. Carrier-specific aliases map onto these."""

    FREIGHT = "FREIGHT"
    FUEL = "FUEL"
    LIFTGATE = "LIFTGATE"
    RESIDENTIAL = "RESIDENTIAL"
    INSIDE_DELIVERY = "INSIDE_DELIVERY"
    DETENTION = "DETENTION"
    REWEIGH = "REWEIGH"
    LIMITED_ACCESS = "LIMITED_ACCESS"
    APPOINTMENT = "APPOINTMENT"
    ADDRESS_CORRECTION = "ADDRESS_CORRECTION"
    OVERSIZE = "OVERSIZE"
    DECLARED_VALUE = "DECLARED_VALUE"
    OTHER = "OTHER"


ACCESSORIAL_CODES: frozenset[str] = frozenset(
    code.value
    for code in ChargeCode
    if code not in {ChargeCode.FREIGHT, ChargeCode.FUEL, ChargeCode.OTHER}
)


class InvoiceStatus(StrEnum):
    RECEIVED = "received"
    AUDITED = "audited"
    APPROVED = "approved"
    DISPUTED = "disputed"
    PAID = "paid"


class ExceptionSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    DISPUTED = "disputed"
    RECOVERED = "recovered"
    DISMISSED = "dismissed"


class AuditRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
