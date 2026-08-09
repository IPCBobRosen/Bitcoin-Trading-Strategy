"""Track Eagle heartbeat freshness and connection health."""

from datetime import datetime, timedelta, timezone


class ConnectionHealth:
    """Track whether Eagle heartbeat activity is still considered healthy."""

    def __init__(
        self,
        heartbeat_timeout_seconds: int = 45,
    ) -> None:
        """Create a connection-health tracker.

        Args:
            heartbeat_timeout_seconds:
                Maximum allowed heartbeat age before Eagle is considered
                unavailable.
        """

        if (
            not isinstance(heartbeat_timeout_seconds, int)
            or isinstance(heartbeat_timeout_seconds, bool)
            or heartbeat_timeout_seconds <= 0
        ):
            raise ValueError(
                "'heartbeat_timeout_seconds' must be a positive integer."
            )

        self._heartbeat_timeout = timedelta(
            seconds=heartbeat_timeout_seconds
        )

        self._last_heartbeat_at: datetime | None = None

    @property
    def last_heartbeat_at(self) -> datetime | None:
        """Return the most recent recorded heartbeat timestamp."""

        return self._last_heartbeat_at

    @property
    def heartbeat_timeout_seconds(self) -> int:
        """Return the configured heartbeat timeout in seconds."""

        return int(
            self._heartbeat_timeout.total_seconds()
        )

    def record_heartbeat(
        self,
        *,
        received_at: datetime | None = None,
    ) -> None:
        """Record receipt of an Eagle heartbeat."""

        if received_at is None:
            received_at = datetime.now(timezone.utc)

        self._validate_timestamp(
            received_at,
            "received_at",
        )

        self._last_heartbeat_at = received_at

    def is_healthy(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return True when the latest heartbeat is still fresh."""

        if self._last_heartbeat_at is None:
            return False

        if now is None:
            now = datetime.now(timezone.utc)

        self._validate_timestamp(
            now,
            "now",
        )

        heartbeat_age = (
            now - self._last_heartbeat_at
        )

        return heartbeat_age <= self._heartbeat_timeout

    def heartbeat_age_seconds(
        self,
        *,
        now: datetime | None = None,
    ) -> float | None:
        """Return the age of the most recent heartbeat in seconds."""

        if self._last_heartbeat_at is None:
            return None

        if now is None:
            now = datetime.now(timezone.utc)

        self._validate_timestamp(
            now,
            "now",
        )

        return (
            now - self._last_heartbeat_at
        ).total_seconds()

    @staticmethod
    def _validate_timestamp(
        value: datetime,
        field_name: str,
    ) -> None:
        """Validate a timezone-aware datetime."""

        if not isinstance(value, datetime):
            raise TypeError(
                f"'{field_name}' must be a datetime."
            )

        if value.tzinfo is None:
            raise ValueError(
                f"'{field_name}' must be timezone-aware."
            )