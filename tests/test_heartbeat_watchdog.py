"""Tests for the Eagle heartbeat watchdog."""

import asyncio

import pytest

from app.connection_health import ConnectionHealth
from app.heartbeat_watchdog import (
    HeartbeatTimeoutError,
    HeartbeatWatchdog,
)


def test_watchdog_accepts_connection_health() -> None:
    """A watchdog should accept a ConnectionHealth instance."""

    health = ConnectionHealth()

    watchdog = HeartbeatWatchdog(
        health,
        check_interval_seconds=1.0,
    )

    assert watchdog.check_interval_seconds == 1.0


def test_invalid_health_is_rejected() -> None:
    """HeartbeatWatchdog requires ConnectionHealth."""

    with pytest.raises(
        TypeError,
        match="'health' must be a ConnectionHealth",
    ):
        HeartbeatWatchdog(  # type: ignore[arg-type]
            "healthy"
        )


def test_invalid_check_interval_is_rejected() -> None:
    """Watchdog polling interval must be positive."""

    health = ConnectionHealth()

    with pytest.raises(
        ValueError,
        match="'check_interval_seconds' must be positive",
    ):
        HeartbeatWatchdog(
            health,
            check_interval_seconds=0,
        )


def test_check_rejects_missing_heartbeat() -> None:
    """No heartbeat should be treated as unavailable."""

    health = ConnectionHealth()

    watchdog = HeartbeatWatchdog(
        health
    )

    with pytest.raises(
        HeartbeatTimeoutError,
        match="has not yet been received",
    ):
        watchdog.check()


def test_check_accepts_healthy_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy ConnectionHealth state should pass the watchdog."""

    health = ConnectionHealth()

    monkeypatch.setattr(
        health,
        "is_healthy",
        lambda: True,
    )

    watchdog = HeartbeatWatchdog(
        health
    )

    watchdog.check()


def test_check_rejects_stale_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale heartbeat should cause a timeout error."""

    health = ConnectionHealth()

    monkeypatch.setattr(
        health,
        "is_healthy",
        lambda: False,
    )

    monkeypatch.setattr(
        health,
        "heartbeat_age_seconds",
        lambda: 46.0,
    )

    watchdog = HeartbeatWatchdog(
        health
    )

    with pytest.raises(
        HeartbeatTimeoutError,
        match="heartbeat is stale",
    ):
        watchdog.check()


def test_monitor_calls_timeout_handler() -> None:
    """Monitor should call its handler when no heartbeat arrives."""

    async def run_test() -> None:
        health = ConnectionHealth()

        watchdog = HeartbeatWatchdog(
            health,
            check_interval_seconds=0.001,
            initial_heartbeat_timeout_seconds=0.003,
        )

        timeout_called = False

        async def on_timeout() -> None:
            nonlocal timeout_called
            timeout_called = True

        await watchdog.monitor(
            on_timeout
        )

        assert timeout_called is True

    asyncio.run(run_test())


def test_monitor_rejects_non_callable_handler() -> None:
    """Monitor requires a callable timeout handler."""

    async def run_test() -> None:
        health = ConnectionHealth()

        watchdog = HeartbeatWatchdog(
            health,
            check_interval_seconds=0.001,
        )

        with pytest.raises(
            TypeError,
            match="'on_timeout' must be callable",
        ):
            await watchdog.monitor(  # type: ignore[arg-type]
                "not-callable"
            )

    asyncio.run(run_test())

def test_initial_heartbeat_timeout_is_configurable() -> None:
    """The initial heartbeat grace period should be configurable."""

    health = ConnectionHealth()

    watchdog = HeartbeatWatchdog(
        health,
        initial_heartbeat_timeout_seconds=30,
    )

    assert watchdog.initial_heartbeat_timeout_seconds == 30.0


def test_invalid_initial_heartbeat_timeout_is_rejected() -> None:
    """Initial heartbeat timeout must be positive."""

    health = ConnectionHealth()

    with pytest.raises(
        ValueError,
        match="'initial_heartbeat_timeout_seconds' must be positive",
    ):
        HeartbeatWatchdog(
            health,
            initial_heartbeat_timeout_seconds=0,
        )