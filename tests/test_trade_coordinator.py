"""Tests for the TradeCoordinator."""

from decimal import Decimal

import pytest

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import Environment, TradeIntent
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)
from app.trade_coordinator import (
    APPROVED,
    INVALID_SIGNAL_LIFECYCLE,
    TRADING_PAUSED,
    TradeCoordinator,
)
from app.trading_controls import TradingControls
from app.daily_loss_guard import DailyLossGuard
from app.kill_switch import KillSwitch
from app.risk_manager import RiskManager


def create_test_event(
    *,
    event_id: str = "test-event-001",
    signal_id: str = "test-signal-001",
    seq: int = 1,
    intent: str = "BUY_TO_OPEN",
) -> IncomingLifecycleEvent:
    """Create a valid accepted lifecycle event for coordinator tests."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": signal_id,
            "ts": "2026-07-20T12:00:00+00:00",
            "env": "staging",
            "payload": {
                "intent": intent,
            },
        }
    )


def create_controls() -> TradingControls:
    """Create deterministic BTS trading controls."""

    return TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )


def create_coordinator(
    tmp_path,
    *,
    resume: bool = False,
) -> TradeCoordinator:
    """Create coordinator with isolated durable lifecycle state."""

    controls = create_controls()

    if resume:
        controls.resume()

    lifecycle_guard = SignalLifecycleGuard(
        tmp_path
        / "signal_lifecycle.db"
    )

    return TradeCoordinator(
        controls,
        lifecycle_guard,
    )


def test_constructor_requires_trading_controls(
    tmp_path,
) -> None:
    """Coordinator requires TradingControls."""

    guard = SignalLifecycleGuard(
        tmp_path
        / "signal_lifecycle.db"
    )

    with pytest.raises(
        TypeError,
        match="'controls'",
    ):
        TradeCoordinator(
            object(),  # type: ignore[arg-type]
            guard,
        )


def test_constructor_requires_signal_lifecycle_guard() -> None:
    """Coordinator requires durable lifecycle protection."""

    controls = create_controls()

    with pytest.raises(
        TypeError,
        match="'signal_lifecycle_guard'",
    ):
        TradeCoordinator(
            controls,
            object(),  # type: ignore[arg-type]
        )


def test_process_event_rejects_invalid_event_type(
    tmp_path,
) -> None:
    """Coordinator requires IncomingLifecycleEvent."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    with pytest.raises(
        TypeError,
        match="'event'",
    ):
        coordinator.process_event(
            object()  # type: ignore[arg-type]
        )


def test_process_event_rejects_event_when_trading_is_paused(
    tmp_path,
) -> None:
    """A paused trading system must not create a TradeRequest."""

    coordinator = create_coordinator(
        tmp_path,
        resume=False,
    )

    event = create_test_event()

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is False
    assert decision.reason == TRADING_PAUSED
    assert decision.trade_request is None

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            event.signal_id
        )
        is None
    )


def test_process_event_creates_request_when_trading_is_enabled(
    tmp_path,
) -> None:
    """An enabled valid BUY_TO_OPEN should create TradeRequest."""

    controls = create_controls()
    controls.resume()

    expected_settings = (
        controls.create_snapshot()
    )

    lifecycle_guard = SignalLifecycleGuard(
        tmp_path
        / "signal_lifecycle.db"
    )

    coordinator = TradeCoordinator(
        controls,
        lifecycle_guard,
    )

    event = create_test_event()

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is True
    assert decision.reason == APPROVED
    assert decision.trade_request is not None

    request = decision.trade_request

    assert request.event_id == event.event_id
    assert request.signal_id == event.signal_id
    assert request.timestamp == event.timestamp
    assert request.environment == Environment.STAGING
    assert request.intent == TradeIntent.BUY_TO_OPEN

    assert request.symbol == expected_settings.symbol
    assert request.quantity == expected_settings.quantity
    assert (
        request.stop_loss_points
        == Decimal("500")
    )
    assert (
        request.stop_loss_points
        == expected_settings.stop_loss_points
    )

    assert (
        lifecycle_guard.get_state(
            event.signal_id
        )
        is SignalLifecycleState.LONG_OPEN
    )


def test_sell_to_close_before_entry_is_rejected(
    tmp_path,
) -> None:
    """Fresh signal may not SELL_TO_CLOSE before entry."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    event = create_test_event(
        event_id="close-before-entry",
        intent="SELL_TO_CLOSE",
    )

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is False
    assert (
        decision.reason
        == INVALID_SIGNAL_LIFECYCLE
    )
    assert decision.trade_request is None

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            event.signal_id
        )
        is None
    )


def test_buy_to_close_before_entry_is_rejected(
    tmp_path,
) -> None:
    """Fresh signal may not BUY_TO_CLOSE before short entry."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    event = create_test_event(
        event_id="close-before-entry",
        intent="BUY_TO_CLOSE",
    )

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is False
    assert (
        decision.reason
        == INVALID_SIGNAL_LIFECYCLE
    )


def test_second_buy_to_open_same_signal_is_rejected(
    tmp_path,
) -> None:
    """Already-open long signal cannot open again."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    first = coordinator.process_event(
        create_test_event(
            event_id="entry-001",
            seq=1,
            intent="BUY_TO_OPEN",
        )
    )

    second = coordinator.process_event(
        create_test_event(
            event_id="entry-002",
            seq=2,
            intent="BUY_TO_OPEN",
        )
    )

    assert first.approved is True

    assert second.approved is False
    assert (
        second.reason
        == INVALID_SIGNAL_LIFECYCLE
    )

    snapshot = (
        coordinator.signal_lifecycle_guard.get_snapshot(
            "test-signal-001"
        )
    )

    assert snapshot is not None
    assert (
        snapshot.state
        is SignalLifecycleState.LONG_OPEN
    )
    assert snapshot.last_event_id == "entry-001"


def test_valid_long_entry_then_close_is_approved(
    tmp_path,
) -> None:
    """BUY_TO_OPEN followed by SELL_TO_CLOSE is valid."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    entry = coordinator.process_event(
        create_test_event(
            event_id="entry-001",
            seq=1,
            intent="BUY_TO_OPEN",
        )
    )

    close = coordinator.process_event(
        create_test_event(
            event_id="close-001",
            seq=2,
            intent="SELL_TO_CLOSE",
        )
    )

    assert entry.approved is True
    assert close.approved is True
    assert close.trade_request is not None

    assert (
        close.trade_request.intent
        is TradeIntent.SELL_TO_CLOSE
    )

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            "test-signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_valid_short_entry_then_close_is_approved(
    tmp_path,
) -> None:
    """SELL_TO_OPEN followed by BUY_TO_CLOSE is valid."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    entry = coordinator.process_event(
        create_test_event(
            event_id="entry-001",
            seq=1,
            intent="SELL_TO_OPEN",
        )
    )

    close = coordinator.process_event(
        create_test_event(
            event_id="close-001",
            seq=2,
            intent="BUY_TO_CLOSE",
        )
    )

    assert entry.approved is True
    assert close.approved is True

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            "test-signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_second_close_after_long_signal_closed_is_rejected(
    tmp_path,
) -> None:
    """Closed signal cannot emit a second SELL_TO_CLOSE."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    coordinator.process_event(
        create_test_event(
            event_id="entry-001",
            seq=1,
            intent="BUY_TO_OPEN",
        )
    )

    coordinator.process_event(
        create_test_event(
            event_id="close-001",
            seq=2,
            intent="SELL_TO_CLOSE",
        )
    )

    second_close = coordinator.process_event(
        create_test_event(
            event_id="close-002",
            seq=3,
            intent="SELL_TO_CLOSE",
        )
    )

    assert second_close.approved is False
    assert (
        second_close.reason
        == INVALID_SIGNAL_LIFECYCLE
    )


def test_closed_signal_cannot_reopen(
    tmp_path,
) -> None:
    """One Eagle signal_id represents one trade lifecycle."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    coordinator.process_event(
        create_test_event(
            event_id="entry-001",
            seq=1,
            intent="BUY_TO_OPEN",
        )
    )

    coordinator.process_event(
        create_test_event(
            event_id="close-001",
            seq=2,
            intent="SELL_TO_CLOSE",
        )
    )

    reopen = coordinator.process_event(
        create_test_event(
            event_id="reopen-001",
            seq=3,
            intent="BUY_TO_OPEN",
        )
    )

    assert reopen.approved is False
    assert (
        reopen.reason
        == INVALID_SIGNAL_LIFECYCLE
    )

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            "test-signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_different_signal_can_open_independently(
    tmp_path,
) -> None:
    """Lifecycle state is isolated by signal_id."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    first = coordinator.process_event(
        create_test_event(
            event_id="entry-a",
            signal_id="signal-a",
            seq=1,
            intent="BUY_TO_OPEN",
        )
    )

    second = coordinator.process_event(
        create_test_event(
            event_id="entry-b",
            signal_id="signal-b",
            seq=2,
            intent="SELL_TO_OPEN",
        )
    )

    assert first.approved is True
    assert second.approved is True

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            "signal-a"
        )
        is SignalLifecycleState.LONG_OPEN
    )

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            "signal-b"
        )
        is SignalLifecycleState.SHORT_OPEN
    )


def test_lifecycle_state_survives_coordinator_restart(
    tmp_path,
) -> None:
    """A restarted coordinator must honor prior durable state."""

    database_path = (
        tmp_path
        / "signal_lifecycle.db"
    )

    controls = create_controls()
    controls.resume()

    first_coordinator = TradeCoordinator(
        controls,
        SignalLifecycleGuard(
            database_path
        ),
    )

    first_coordinator.process_event(
        create_test_event(
            event_id="entry-001",
            seq=1,
            intent="BUY_TO_OPEN",
        )
    )

    restarted_coordinator = TradeCoordinator(
        controls,
        SignalLifecycleGuard(
            database_path
        ),
    )

    duplicate_open = (
        restarted_coordinator.process_event(
            create_test_event(
                event_id="entry-002",
                seq=2,
                intent="BUY_TO_OPEN",
            )
        )
    )

    assert duplicate_open.approved is False
    assert (
        duplicate_open.reason
        == INVALID_SIGNAL_LIFECYCLE
    )

    close = (
        restarted_coordinator.process_event(
            create_test_event(
                event_id="close-001",
                seq=3,
                intent="SELL_TO_CLOSE",
            )
        )
    )

    assert close.approved is True

    assert (
        restarted_coordinator.signal_lifecycle_guard.get_state(
            "test-signal-001"
        )
        is SignalLifecycleState.CLOSED
    )

def test_prepare_event_creates_request_without_mutating_lifecycle(
    tmp_path,
) -> None:
    """Preparing a trade must not mutate durable signal lifecycle."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    event = create_test_event(
        event_id="prepared-entry-001",
        signal_id="prepared-signal-001",
        seq=1,
        intent="BUY_TO_OPEN",
    )

    decision = coordinator.prepare_event(
        event
    )

    assert decision.approved is True
    assert decision.reason == APPROVED
    assert decision.trade_request is not None

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            event.signal_id
        )
        is None
    )


def test_commit_request_mutates_lifecycle_after_preparation(
    tmp_path,
) -> None:
    """A prepared request may commit lifecycle after external approval."""

    coordinator = create_coordinator(
        tmp_path,
        resume=True,
    )

    event = create_test_event(
        event_id="prepared-entry-001",
        signal_id="prepared-signal-001",
        seq=1,
        intent="BUY_TO_OPEN",
    )

    prepared = coordinator.prepare_event(
        event
    )

    assert prepared.trade_request is not None

    committed = coordinator.commit_request(
        prepared.trade_request
    )

    assert committed.approved is True
    assert committed.reason == APPROVED
    assert committed.trade_request is not None

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            event.signal_id
        )
        is SignalLifecycleState.LONG_OPEN
    )

def test_kill_switch_rejection_leaves_lifecycle_unmodified(
    tmp_path,
) -> None:
    """Rejected opening trade must not create durable open lifecycle."""

    controls = create_controls()
    controls.resume()

    lifecycle_guard = SignalLifecycleGuard(
        tmp_path
        / "signal_lifecycle.db"
    )

    coordinator = TradeCoordinator(
        controls,
        lifecycle_guard,
    )

    kill_switch = KillSwitch()

    risk_manager = RiskManager(
        controls,
        kill_switch,
        DailyLossGuard(
            Decimal("1000")
        ),
        allowed_symbols=("MBT",),
        max_order_quantity=1,
        max_absolute_position=1,
    )

    event = create_test_event(
        event_id="kill-switch-entry-001",
        signal_id="kill-switch-signal-001",
        seq=1,
        intent="BUY_TO_OPEN",
    )

    prepared = coordinator.prepare_event(
        event
    )

    assert prepared.approved is True
    assert prepared.trade_request is not None

    # Preparation itself must not mutate lifecycle.
    assert (
        lifecycle_guard.get_state(
            event.signal_id
        )
        is None
    )

    kill_switch.activate(
        "IB error 1100: Connectivity between "
        "IBKR and Trader Workstation has been lost."
    )

    risk_decision = risk_manager.evaluate(
        prepared.trade_request,
        current_position=0,
    )

    assert risk_decision.approved is False
    assert (
        "Emergency kill switch is active"
        in risk_decision.reason
    )

    # Most important regression assertion:
    # rejected risk means commit_request() was never reached,
    # therefore BTS must still have no durable open lifecycle.
    assert (
        lifecycle_guard.get_state(
            event.signal_id
        )
        is None
    )
