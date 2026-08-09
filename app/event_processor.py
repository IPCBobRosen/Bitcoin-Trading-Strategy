"""Process Eagle event identity and sequence state before trade evaluation."""

from dataclasses import dataclass
from enum import Enum

from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_store import EventProcessingResult, EventStore


class EventProcessStatus(Enum):
    """Result of processing Eagle event identity and ordering."""

    ACCEPTED = "Accepted"
    DUPLICATE_EVENT = "DuplicateEvent"
    OUT_OF_SEQUENCE = "OutOfSequence"


@dataclass(frozen=True, slots=True)
class EventProcessResult:
    """Outcome of durable Eagle event processing."""

    status: EventProcessStatus

    @property
    def accepted(self) -> bool:
        """Return True when the event is safe to continue processing."""

        return self.status is EventProcessStatus.ACCEPTED


class EventProcessor:
    """Apply durable event-id and sequence checks to Eagle events."""

    def __init__(self, event_store: EventStore) -> None:
        """Create an event processor backed by persistent storage."""

        if not isinstance(event_store, EventStore):
            raise TypeError("'event_store' must be an EventStore.")

        self._event_store = event_store

    def process(
        self,
        event: IncomingLifecycleEvent,
    ) -> EventProcessResult:
        """Durably evaluate one Eagle lifecycle event."""

        if not isinstance(event, IncomingLifecycleEvent):
            raise TypeError(
                "'event' must be an IncomingLifecycleEvent."
            )

        store_result = self._event_store.check_and_mark_event_with_seq(
            event.event_id,
            event.seq,
        )

        if store_result is EventProcessingResult.ACCEPTED:
            return EventProcessResult(
                status=EventProcessStatus.ACCEPTED,
            )

        if store_result is EventProcessingResult.DUPLICATE_EVENT:
            return EventProcessResult(
                status=EventProcessStatus.DUPLICATE_EVENT,
            )

        if store_result is EventProcessingResult.OUT_OF_SEQUENCE:
            return EventProcessResult(
                status=EventProcessStatus.OUT_OF_SEQUENCE,
            )

        raise RuntimeError(
            f"Unsupported EventProcessingResult: {store_result!r}"
        )