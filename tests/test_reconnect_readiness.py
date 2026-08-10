"""Tests for reconnect readiness evaluation."""

from datetime import datetime, timezone

import pytest

from app.communications.eagle_hello import EagleHello
from app.connection_health import ConnectionHealth
from app.reconciliation_manager import ReconciliationManager
from app.reconnect_readiness import (
    ReconnectReadiness,
    ReconnectReadinessStatus,
)
from app.replay_tracker import ReplayTracker


def create_hello(
    *,
    replay_count: int = 0,
) -> EagleHello:
    """Create a valid hello frame for readiness tests."""

    return EagleHello.from_dict(
        {
            "type": "fund.hello",
            "contract": "1.2.0",
            "version": "1.2.0",
            "capabilities": [],
            "flags": {},
            "last_seq": 5,
            "since_seq": 5,
            "open_count": 0,
            "open": [],
            "replay_count": replay_count,
            "ts": "2026-08-10T12:00:00+00:00",
            "env": "staging",
        }
    )


def create_components() -> tuple[
    ReplayTracker,
    ReconciliationManager,
    ConnectionHealth,
]:
    """Create clean readiness dependencies."""

    replay_tracker = ReplayTracker()
    reconciliation_manager = ReconciliationManager()

    connection_health = ConnectionHealth(
        heartbeat_timeout_seconds=45
    )

    return (
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )


def test_invalid_replay_tracker_is_rejected() -> None:
    """ReconnectReadiness requires ReplayTracker."""

    _, reconciliation_manager, connection_health = (
        create_components()
    )

    with pytest.raises(
        TypeError,
        match="'replay_tracker' must be a ReplayTracker",
    ):
        ReconnectReadiness(
            "invalid",  # type: ignore[arg-type]
            reconciliation_manager,
            connection_health,
        )


def test_invalid_reconciliation_manager_is_rejected() -> None:
    """ReconnectReadiness requires ReconciliationManager."""

    replay_tracker, _, connection_health = (
        create_components()
    )

    with pytest.raises(
        TypeError,
        match=(
            "'reconciliation_manager' must be a "
            "ReconciliationManager"
        ),
    ):
        ReconnectReadiness(
            replay_tracker,
            "invalid",  # type: ignore[arg-type]
            connection_health,
        )


def test_invalid_connection_health_is_rejected() -> None:
    """ReconnectReadiness requires ConnectionHealth."""

    replay_tracker, reconciliation_manager, _ = (
        create_components()
    )

    with pytest.raises(
        TypeError,
        match="'connection_health' must be a ConnectionHealth",
    ):
        ReconnectReadiness(
            replay_tracker,
            reconciliation_manager,
            "invalid",  # type: ignore[arg-type]
        )


def test_not_ready_before_hello() -> None:
    """BTS must not be ready before fund.hello."""

    replay_tracker, reconciliation_manager, connection_health = (
        create_components()
    )

    reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )

    connection_health.record_heartbeat(
        received_at=datetime.now(timezone.utc)
    )

    readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )

    result = readiness.evaluate()

    assert result.status is ReconnectReadinessStatus.NOT_READY
    assert result.ready is False
    assert result.reason == "Eagle fund.hello has not been received."


def test_not_ready_until_replay_complete() -> None:
    """Incomplete replay must block readiness."""

    replay_tracker, reconciliation_manager, connection_health = (
        create_components()
    )

    replay_tracker.process_hello(
        create_hello(
            replay_count=1
        )
    )

    reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )

    connection_health.record_heartbeat(
        received_at=datetime.now(timezone.utc)
    )

    readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )

    result = readiness.evaluate()

    assert result.status is ReconnectReadinessStatus.NOT_READY
    assert result.reason == "Eagle replay has not completed."


def test_not_ready_before_reconciliation() -> None:
    """Unperformed reconciliation must block readiness."""

    replay_tracker, reconciliation_manager, connection_health = (
        create_components()
    )

    replay_tracker.process_hello(
        create_hello()
    )

    connection_health.record_heartbeat(
        received_at=datetime.now(timezone.utc)
    )

    readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )

    result = readiness.evaluate()

    assert result.status is ReconnectReadinessStatus.NOT_READY
    assert result.reason == (
        "Open-position reconciliation is not matched."
    )


def test_mismatched_reconciliation_blocks_readiness() -> None:
    """A broker/Eagle mismatch must block readiness."""

    replay_tracker, reconciliation_manager, connection_health = (
        create_components()
    )

    replay_tracker.process_hello(
        create_hello()
    )

    reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=[
            {
                "symbol": "MBT",
                "side": "LONG",
                "quantity": 1,
            }
        ],
    )

    connection_health.record_heartbeat(
        received_at=datetime.now(timezone.utc)
    )

    readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )

    result = readiness.evaluate()

    assert result.status is ReconnectReadinessStatus.NOT_READY
    assert result.ready is False
    assert result.reason == (
        "Open-position reconciliation is not matched."
    )


def test_unhealthy_heartbeat_blocks_readiness() -> None:
    """Heartbeat health must be current before BTS can be ready."""

    replay_tracker, reconciliation_manager, connection_health = (
        create_components()
    )

    replay_tracker.process_hello(
        create_hello()
    )

    reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )

    readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )

    result = readiness.evaluate()

    assert result.status is ReconnectReadinessStatus.NOT_READY
    assert result.ready is False
    assert result.reason == "Eagle heartbeat is not healthy."


def test_all_conditions_satisfied_is_ready() -> None:
    """All reconnect safety requirements should produce READY."""

    replay_tracker, reconciliation_manager, connection_health = (
        create_components()
    )

    replay_tracker.process_hello(
        create_hello()
    )

    reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )

    connection_health.record_heartbeat(
        received_at=datetime.now(timezone.utc)
    )

    readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        connection_health,
    )

    result = readiness.evaluate()

    assert result.status is ReconnectReadinessStatus.READY
    assert result.ready is True
    assert result.reason == (
        "All reconnect safety conditions are satisfied."
    )