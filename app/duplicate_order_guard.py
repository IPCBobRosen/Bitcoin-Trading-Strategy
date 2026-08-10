"""Prevent duplicate BTS order submissions."""

from dataclasses import dataclass
from enum import Enum

from app.communications.trade_request import TradeRequest


class DuplicateOrderStatus(Enum):
    """Outcome of attempting to reserve one TradeRequest."""

    RESERVED = "Reserved"
    DUPLICATE = "Duplicate"


@dataclass(frozen=True, slots=True)
class DuplicateOrderDecision:
    """Immutable result of one duplicate-order check."""

    status: DuplicateOrderStatus
    event_id: str
    signal_id: str
    reason: str

    @property
    def allowed(self) -> bool:
        """Return True when the event was newly reserved."""

        return self.status is DuplicateOrderStatus.RESERVED


@dataclass(frozen=True, slots=True)
class DuplicateOrderSnapshot:
    """Immutable snapshot of reserved Eagle event IDs."""

    reserved_event_ids: tuple[str, ...]
    reservation_count: int


class DuplicateOrderGuard:
    """Reserve Eagle event IDs to prevent duplicate order submission.

    The Eagle event_id is treated as the execution idempotency key.

    Different events may legitimately share the same signal_id.
    For example, an entry and its later exit may belong to the
    same signal lifecycle.

    Therefore signal_id is retained for audit information but is
    not itself used to reject a TradeRequest as a duplicate.
    """

    def __init__(self) -> None:
        """Create an empty duplicate-order guard."""

        self._reservations: dict[str, str] = {}

    @property
    def reservation_count(self) -> int:
        """Return the number of currently reserved event IDs."""

        return len(
            self._reservations
        )

    def reserve(
        self,
        trade_request: TradeRequest,
    ) -> DuplicateOrderDecision:
        """Reserve a TradeRequest event ID before broker submission.

        The first reservation for an event_id succeeds.

        A later reservation using the same event_id is rejected,
        even if another TradeRequest object was constructed from
        the same Eagle event.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        event_id = self._validate_identifier(
            trade_request.event_id,
            "event_id",
        )

        signal_id = self._validate_identifier(
            trade_request.signal_id,
            "signal_id",
        )

        existing_signal_id = (
            self._reservations.get(
                event_id
            )
        )

        if existing_signal_id is not None:
            return DuplicateOrderDecision(
                status=DuplicateOrderStatus.DUPLICATE,
                event_id=event_id,
                signal_id=signal_id,
                reason=(
                    f"Eagle event {event_id!r} has already "
                    "been reserved for order submission."
                ),
            )

        self._reservations[
            event_id
        ] = signal_id

        return DuplicateOrderDecision(
            status=DuplicateOrderStatus.RESERVED,
            event_id=event_id,
            signal_id=signal_id,
            reason=(
                f"Eagle event {event_id!r} was reserved "
                "for order submission."
            ),
        )

    def contains(
        self,
        event_id: str,
    ) -> bool:
        """Return True when an Eagle event ID is reserved."""

        normalized_event_id = (
            self._validate_identifier(
                event_id,
                "event_id",
            )
        )

        return (
            normalized_event_id
            in self._reservations
        )

    def release(
        self,
        event_id: str,
    ) -> bool:
        """Release an event reservation.

        This is intended for a future execution path where BTS
        reserves an event but fails locally before the order is
        actually submitted to the broker.

        Returns:
            True when a reservation existed and was removed.
            False when the event ID was not reserved.
        """

        normalized_event_id = (
            self._validate_identifier(
                event_id,
                "event_id",
            )
        )

        if (
            normalized_event_id
            not in self._reservations
        ):
            return False

        del self._reservations[
            normalized_event_id
        ]

        return True

    def clear(self) -> None:
        """Clear every reservation.

        This should not be used casually in live execution.
        It exists primarily for deterministic session/test setup.
        """

        self._reservations.clear()

    def snapshot(
        self,
    ) -> DuplicateOrderSnapshot:
        """Return an immutable reservation snapshot."""

        reserved_event_ids = tuple(
            sorted(
                self._reservations.keys()
            )
        )

        return DuplicateOrderSnapshot(
            reserved_event_ids=reserved_event_ids,
            reservation_count=len(
                reserved_event_ids
            ),
        )

    @staticmethod
    def _validate_identifier(
        value: str,
        field_name: str,
    ) -> str:
        """Validate and normalize an Eagle identifier."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                f"'{field_name}' must be a non-empty string."
            )

        return value.strip()