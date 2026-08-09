"""Process Eagle heartbeat control frames."""

from app.communications.eagle_heartbeat import EagleHeartbeat
from app.event_store import EventStore


class HeartbeatProcessor:
    """Persist sequence progress from Eagle heartbeat messages.

    Heartbeats are control messages, not trading instructions.

    Processing a heartbeat may advance BTS's durable Eagle sequence
    cursor, but it must never create a TradeRequest or reach the
    TradeCoordinator.
    """

    def __init__(self, event_store: EventStore) -> None:
        """Create a heartbeat processor using persistent event storage."""

        if not isinstance(event_store, EventStore):
            raise TypeError(
                "'event_store' must be an EventStore."
            )

        self._event_store = event_store

    def process(self, heartbeat: EagleHeartbeat) -> None:
        """Persist the heartbeat sequence when it advances the cursor."""

        if not isinstance(heartbeat, EagleHeartbeat):
            raise TypeError(
                "'heartbeat' must be an EagleHeartbeat."
            )

        self._event_store.mark_seq_processed(
            heartbeat.seq
        )