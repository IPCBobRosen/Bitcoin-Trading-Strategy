"""Track Interactive Brokers API handshake readiness."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IBApiReadySnapshot:
    """Immutable snapshot of IBKR API handshake readiness."""

    ready: bool
    next_valid_order_id: int | None


class IBApiReady:
    """Track whether the IBKR API handshake has completed."""

    def __init__(self) -> None:
        """Create a readiness tracker in the disconnected state."""

        self._ready = False
        self._next_valid_order_id: int | None = None

    @property
    def ready(self) -> bool:
        """Return True after nextValidId has been received."""

        return self._ready

    @property
    def next_valid_order_id(self) -> int | None:
        """Return the most recent IBKR next valid order ID."""

        return self._next_valid_order_id

    def record_next_valid_id(
        self,
        order_id: int,
    ) -> None:
        """Record IBKR's nextValidId callback.

        The callback is used here only as proof that the API
        handshake has completed.

        BTS does not use this component to place orders.
        """

        if (
            not isinstance(order_id, int)
            or isinstance(order_id, bool)
            or order_id < 0
        ):
            raise ValueError(
                "'order_id' must be a non-negative integer."
            )

        self._next_valid_order_id = order_id
        self._ready = True

    def reset(self) -> None:
        """Reset readiness when the IBKR connection is lost."""

        self._ready = False
        self._next_valid_order_id = None

    def snapshot(self) -> IBApiReadySnapshot:
        """Return the current immutable readiness state."""

        return IBApiReadySnapshot(
            ready=self._ready,
            next_valid_order_id=(
                self._next_valid_order_id
            ),
        )