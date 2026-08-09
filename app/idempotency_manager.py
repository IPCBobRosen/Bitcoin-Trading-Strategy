"""Track Eagle event IDs so duplicate events are processed only once."""


class IdempotencyManager:
    """Track event IDs that BTS has already processed.

    This first version stores event IDs in memory only.

    A later production version will persist event IDs so duplicate protection
    survives application restarts, reconnects, and computer restarts.
    """

    def __init__(self) -> None:
        """Create an empty idempotency manager."""

        self._processed_event_ids: set[str] = set()

    def has_processed(self, event_id: str) -> bool:
        """Return True if the event ID has already been processed."""

        self._validate_event_id(event_id)

        return event_id in self._processed_event_ids

    def mark_processed(self, event_id: str) -> None:
        """Record an event ID as processed."""

        self._validate_event_id(event_id)

        self._processed_event_ids.add(event_id)

    def check_and_mark(self, event_id: str) -> bool:
        """Atomically check whether an event is new and record it.

        Returns:
            True:
                The event ID had not been seen before and is now recorded.

            False:
                The event ID had already been processed.
        """

        self._validate_event_id(event_id)

        if event_id in self._processed_event_ids:
            return False

        self._processed_event_ids.add(event_id)

        return True

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        """Validate an Eagle event ID."""

        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("'event_id' must be a non-empty string.")