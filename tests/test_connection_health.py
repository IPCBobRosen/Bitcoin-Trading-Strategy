"""Tests for Eagle connection-health tracking."""

from datetime import datetime, timedelta, timezone

import pytest

from app.connection_health import ConnectionHealth


BASE_TIME = datetime(
    2026,
    8,
    9,
    20,
    0,
    0,
    tzinfo=timezone.utc,
)


def test_new_health_tracker_is_not_healthy() -> None:
    """No heartbeat means Eagle is not yet considered healthy."""

    health = ConnectionHealth()

    assert health.is_healthy(now=BASE_TIME) is False
    assert health.last_heartbeat_at is None


def test_recorded_heartbeat_makes_connection_healthy() -> None:
    """A fresh heartbeat should mark Eagle healthy."""

    health = ConnectionHealth()

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    assert health.is_healthy(
        now=BASE_TIME
    ) is True


def test_heartbeat_is_healthy_before_timeout() -> None:
    """Heartbeat age below 45 seconds should remain healthy."""

    health = ConnectionHealth()

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    now = BASE_TIME + timedelta(
        seconds=44
    )

    assert health.is_healthy(
        now=now
    ) is True


def test_heartbeat_is_healthy_at_exact_timeout() -> None:
    """Heartbeat age equal to the timeout should remain healthy."""

    health = ConnectionHealth()

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    now = BASE_TIME + timedelta(
        seconds=45
    )

    assert health.is_healthy(
        now=now
    ) is True


def test_heartbeat_becomes_stale_after_timeout() -> None:
    """Heartbeat age above 45 seconds should be unhealthy."""

    health = ConnectionHealth()

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    now = BASE_TIME + timedelta(
        seconds=46
    )

    assert health.is_healthy(
        now=now
    ) is False


def test_new_heartbeat_restores_health() -> None:
    """A later heartbeat should restore healthy state."""

    health = ConnectionHealth()

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    stale_time = BASE_TIME + timedelta(
        seconds=60
    )

    assert health.is_healthy(
        now=stale_time
    ) is False

    health.record_heartbeat(
        received_at=stale_time
    )

    assert health.is_healthy(
        now=stale_time
    ) is True


def test_heartbeat_age_is_reported() -> None:
    """Heartbeat age should be available for logging and diagnostics."""

    health = ConnectionHealth()

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    now = BASE_TIME + timedelta(
        seconds=12
    )

    assert health.heartbeat_age_seconds(
        now=now
    ) == 12.0


def test_heartbeat_age_is_none_before_first_heartbeat() -> None:
    """Heartbeat age is unknown before any heartbeat arrives."""

    health = ConnectionHealth()

    assert health.heartbeat_age_seconds(
        now=BASE_TIME
    ) is None


def test_custom_timeout_is_supported() -> None:
    """The timeout value should be configurable for testing and staging."""

    health = ConnectionHealth(
        heartbeat_timeout_seconds=10
    )

    assert health.heartbeat_timeout_seconds == 10

    health.record_heartbeat(
        received_at=BASE_TIME
    )

    assert health.is_healthy(
        now=BASE_TIME + timedelta(seconds=11)
    ) is False


def test_invalid_timeout_is_rejected() -> None:
    """Heartbeat timeout must be a positive integer."""

    with pytest.raises(
        ValueError,
        match="'heartbeat_timeout_seconds' must be a positive integer",
    ):
        ConnectionHealth(
            heartbeat_timeout_seconds=0
        )


def test_naive_heartbeat_timestamp_is_rejected() -> None:
    """Heartbeat timestamps must contain timezone information."""

    health = ConnectionHealth()

    naive_timestamp = datetime(
        2026,
        8,
        9,
        20,
        0,
        0,
    )

    with pytest.raises(
        ValueError,
        match="'received_at' must be timezone-aware",
    ):
        health.record_heartbeat(
            received_at=naive_timestamp
        )