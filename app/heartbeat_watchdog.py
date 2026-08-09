"""Monitor Eagle connection health for stale heartbeats."""

import asyncio
from collections.abc import Awaitable, Callable

from app.connection_health import ConnectionHealth


class HeartbeatTimeoutError(ConnectionError):
    """Raised when Eagle heartbeat activity becomes stale."""


class HeartbeatWatchdog:
    """Periodically check whether Eagle heartbeat activity is healthy."""

    def __init__(
        self,
        health: ConnectionHealth,
        *,
        check_interval_seconds: float = 1.0,
        initial_heartbeat_timeout_seconds: float = 45.0,
    ) -> None:
        """Create a heartbeat watchdog.

        Args:
            health:
                Connection-health tracker containing heartbeat state.

            check_interval_seconds:
                Number of seconds between health checks.

            initial_heartbeat_timeout_seconds:
                Maximum time to wait for the first heartbeat after the
                watchdog starts.
        """

        if not isinstance(health, ConnectionHealth):
            raise TypeError(
                "'health' must be a ConnectionHealth."
            )

        if (
            not isinstance(check_interval_seconds, (int, float))
            or isinstance(check_interval_seconds, bool)
            or check_interval_seconds <= 0
        ):
            raise ValueError(
                "'check_interval_seconds' must be positive."
            )

        if (
            not isinstance(
                initial_heartbeat_timeout_seconds,
                (int, float),
            )
            or isinstance(
                initial_heartbeat_timeout_seconds,
                bool,
            )
            or initial_heartbeat_timeout_seconds <= 0
        ):
            raise ValueError(
                "'initial_heartbeat_timeout_seconds' must be positive."
            )

        self._health = health

        self._check_interval_seconds = float(
            check_interval_seconds
        )

        self._initial_heartbeat_timeout_seconds = float(
            initial_heartbeat_timeout_seconds
        )

    @property
    def check_interval_seconds(self) -> float:
        """Return the watchdog polling interval."""

        return self._check_interval_seconds

    @property
    def initial_heartbeat_timeout_seconds(self) -> float:
        """Return the allowed wait for the first heartbeat."""

        return self._initial_heartbeat_timeout_seconds

    def check(self) -> None:
        """Check heartbeat health once.

        Raises:
            HeartbeatTimeoutError:
                If no current heartbeat exists or the latest heartbeat
                has become stale.
        """

        if self._health.is_healthy():
            return

        heartbeat_age = (
            self._health.heartbeat_age_seconds()
        )

        if heartbeat_age is None:
            raise HeartbeatTimeoutError(
                "Eagle heartbeat has not yet been received."
            )

        raise HeartbeatTimeoutError(
            "Eagle heartbeat is stale. "
            f"Last heartbeat age: {heartbeat_age:.2f} seconds."
        )

    async def monitor(
        self,
        on_timeout: Callable[[], Awaitable[None]],
    ) -> None:
        """Monitor heartbeat health until a timeout occurs.

        The first heartbeat receives an initial grace period.

        After the first heartbeat has been received, normal heartbeat
        freshness rules are applied.

        When heartbeat health becomes unavailable, the supplied
        asynchronous timeout handler is called once and the monitor exits.
        """

        if not callable(on_timeout):
            raise TypeError(
                "'on_timeout' must be callable."
            )

        loop = asyncio.get_running_loop()
        started_at = loop.time()

        while True:
            await asyncio.sleep(
                self._check_interval_seconds
            )

            if self._health.last_heartbeat_at is None:
                elapsed = (
                    loop.time() - started_at
                )

                if (
                    elapsed
                    <= self._initial_heartbeat_timeout_seconds
                ):
                    continue

                await on_timeout()
                return

            try:
                self.check()

            except HeartbeatTimeoutError:
                await on_timeout()
                return