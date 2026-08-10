"""Tests for safe BTS trading resume control."""

import pytest

from app.communications.eagle_hello import EagleHello
from app.connection_health import ConnectionHealth
from app.reconciliation_manager import ReconciliationManager
from app.reconnect_readiness import ReconnectReadiness
from app.replay_tracker import ReplayTracker
from app.resume_manager import (
    ResumeManager,
    ResumeStatus,
)
from app.trading_controls import TradingControls


def create_components() -> tuple[
    TradingControls,
    ConnectionHealth,
    ReplayTracker,
    ReconciliationManager,
    ReconnectReadiness,
    ResumeManager,
]:
    """Create the components required for resume testing."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    health = ConnectionHealth(
        heartbeat_timeout_seconds=45
    )

    replay_tracker = ReplayTracker()

    reconciliation_manager = ReconciliationManager()

    reconnect_readiness = ReconnectReadiness(
        replay_tracker,
        reconciliation_manager,
        health,
    )

    resume_manager = ResumeManager(
        controls,
        reconnect_readiness,
    )

    return (
        controls,
        health,
        replay_tracker,
        reconciliation_manager,
        reconnect_readiness,
        resume_manager,
    )


def make_replay_complete(
    replay_tracker: ReplayTracker,
) -> None:
    """Mark replay as complete with zero expected events."""

    hello = EagleHello.from_dict(
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
            "replay_count": 0,
            "ts": "2026-08-10T12:00:00+00:00",
            "env": "staging",
        }
    )

    replay_tracker.process_hello(
        hello
    )


def make_reconciliation_match(
    reconciliation_manager: ReconciliationManager,
) -> None:
    """Create a successful empty-book reconciliation."""

    reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )


def test_new_controls_begin_paused() -> None:
    """Trading controls used by resume tests should start paused."""

    (
        controls,
        _,
        _,
        _,
        _,
        _,
    ) = create_components()

    assert controls.is_paused is True


def test_resume_is_rejected_before_replay_completion() -> None:
    """Trading cannot resume before replay has completed."""

    (
        controls,
        health,
        _,
        reconciliation_manager,
        _,
        resume_manager,
    ) = create_components()

    make_reconciliation_match(
        reconciliation_manager
    )

    health.record_heartbeat()

    result = resume_manager.request_resume()

    assert result.status is ResumeStatus.REJECTED
    assert result.resumed is False
    assert controls.is_paused is True


def test_resume_is_rejected_without_reconciliation() -> None:
    """Trading cannot resume before position reconciliation."""

    (
        controls,
        health,
        replay_tracker,
        _,
        _,
        resume_manager,
    ) = create_components()

    make_replay_complete(
        replay_tracker
    )

    health.record_heartbeat()

    result = resume_manager.request_resume()

    assert result.status is ResumeStatus.REJECTED
    assert result.resumed is False
    assert controls.is_paused is True


def test_resume_is_rejected_for_position_mismatch() -> None:
    """Trading cannot resume when broker positions disagree."""

    (
        controls,
        health,
        replay_tracker,
        reconciliation_manager,
        _,
        resume_manager,
    ) = create_components()

    make_replay_complete(
        replay_tracker
    )

    reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "signal_id": "signal-001",
                "symbol": "MBT",
                "side": "LONG",
                "quantity": 1,
            }
        ],
        broker_positions=[],
    )

    health.record_heartbeat()

    result = resume_manager.request_resume()

    assert result.status is ResumeStatus.REJECTED
    assert result.resumed is False
    assert controls.is_paused is True


def test_resume_is_rejected_without_healthy_heartbeat() -> None:
    """Trading cannot resume without a healthy Eagle heartbeat."""

    (
        controls,
        _,
        replay_tracker,
        reconciliation_manager,
        _,
        resume_manager,
    ) = create_components()

    make_replay_complete(
        replay_tracker
    )

    make_reconciliation_match(
        reconciliation_manager
    )

    result = resume_manager.request_resume()

    assert result.status is ResumeStatus.REJECTED
    assert result.resumed is False
    assert controls.is_paused is True


def test_resume_succeeds_when_all_conditions_are_ready() -> None:
    """Trading may resume only after all safety gates pass."""

    (
        controls,
        health,
        replay_tracker,
        reconciliation_manager,
        _,
        resume_manager,
    ) = create_components()

    make_replay_complete(
        replay_tracker
    )

    make_reconciliation_match(
        reconciliation_manager
    )

    health.record_heartbeat()

    result = resume_manager.request_resume()

    assert result.status is ResumeStatus.RESUMED
    assert result.resumed is True
    assert controls.is_paused is False


def test_successful_resume_has_explanatory_reason() -> None:
    """Successful resume should provide an audit-friendly reason."""

    (
        _,
        health,
        replay_tracker,
        reconciliation_manager,
        _,
        resume_manager,
    ) = create_components()

    make_replay_complete(
        replay_tracker
    )

    make_reconciliation_match(
        reconciliation_manager
    )

    health.record_heartbeat()

    result = resume_manager.request_resume()

    assert "safety conditions" in result.reason.lower()


def test_rejected_resume_has_explanatory_reason() -> None:
    """Rejected resume should explain the failed safety condition."""

    (
        _,
        _,
        _,
        _,
        _,
        resume_manager,
    ) = create_components()

    result = resume_manager.request_resume()

    assert result.status is ResumeStatus.REJECTED
    assert "rejected" in result.reason.lower()


def test_invalid_controls_are_rejected() -> None:
    """ResumeManager requires TradingControls."""

    (
        _,
        _,
        _,
        _,
        reconnect_readiness,
        _,
    ) = create_components()

    with pytest.raises(
        TypeError,
        match="'controls' must be a TradingControls",
    ):
        ResumeManager(
            object(),  # type: ignore[arg-type]
            reconnect_readiness,
        )


def test_invalid_reconnect_readiness_is_rejected() -> None:
    """ResumeManager requires ReconnectReadiness."""

    (
        controls,
        _,
        _,
        _,
        _,
        _,
    ) = create_components()

    with pytest.raises(
        TypeError,
        match="'reconnect_readiness' must be a ReconnectReadiness",
    ):
        ResumeManager(
            controls,
            object(),  # type: ignore[arg-type]
        )