"""Tests for durable Eagle signal lifecycle protection."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.signal_lifecycle_guard import (
    SignalLifecycleDecision,
    SignalLifecycleGuard,
    SignalLifecycleState,
    SignalLifecycleStatus,
)


def create_request(
    *,
    event_id: str,
    signal_id: str = "signal-001",
    intent: TradeIntent,
) -> TradeRequest:
    """Create deterministic lifecycle TradeRequest."""

    return TradeRequest(
        event_id=event_id,
        signal_id=signal_id,
        timestamp=datetime(
            2026,
            8,
            16,
            19,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=intent,
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )


def create_guard(
    tmp_path,
) -> SignalLifecycleGuard:
    """Create isolated durable lifecycle guard."""

    return SignalLifecycleGuard(
        tmp_path
        / "signal_lifecycle.db"
    )


def test_new_buy_to_open_is_accepted(
    tmp_path,
) -> None:
    """New BUY_TO_OPEN should establish long lifecycle."""

    guard = create_guard(
        tmp_path
    )

    result = guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    assert result.allowed is True
    assert (
        result.status
        is SignalLifecycleStatus.ACCEPTED
    )
    assert result.previous_state is None
    assert (
        result.next_state
        is SignalLifecycleState.LONG_OPEN
    )

    assert (
        guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.LONG_OPEN
    )


def test_new_sell_to_open_is_accepted(
    tmp_path,
) -> None:
    """New SELL_TO_OPEN should establish short lifecycle."""

    guard = create_guard(
        tmp_path
    )

    result = guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    assert result.allowed is True
    assert (
        result.next_state
        is SignalLifecycleState.SHORT_OPEN
    )


def test_sell_to_close_before_entry_is_rejected(
    tmp_path,
) -> None:
    """SELL_TO_CLOSE cannot occur before long entry."""

    guard = create_guard(
        tmp_path
    )

    result = guard.process(
        create_request(
            event_id="bad-close-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    assert result.allowed is False
    assert (
        result.status
        is SignalLifecycleStatus.INVALID_TRANSITION
    )
    assert result.previous_state is None
    assert result.next_state is None
    assert guard.get_state(
        "signal-001"
    ) is None


def test_buy_to_close_before_entry_is_rejected(
    tmp_path,
) -> None:
    """BUY_TO_CLOSE cannot occur before short entry."""

    guard = create_guard(
        tmp_path
    )

    result = guard.process(
        create_request(
            event_id="bad-close-001",
            intent=TradeIntent.BUY_TO_CLOSE,
        )
    )

    assert result.allowed is False
    assert guard.get_state(
        "signal-001"
    ) is None


def test_long_position_accepts_sell_to_close(
    tmp_path,
) -> None:
    """Long lifecycle should close with SELL_TO_CLOSE."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    result = guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    assert result.allowed is True
    assert (
        result.previous_state
        is SignalLifecycleState.LONG_OPEN
    )
    assert (
        result.next_state
        is SignalLifecycleState.CLOSED
    )

    assert (
        guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_short_position_accepts_buy_to_close(
    tmp_path,
) -> None:
    """Short lifecycle should close with BUY_TO_CLOSE."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    result = guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.BUY_TO_CLOSE,
        )
    )

    assert result.allowed is True
    assert (
        result.next_state
        is SignalLifecycleState.CLOSED
    )


@pytest.mark.parametrize(
    "invalid_intent",
    [
        TradeIntent.BUY_TO_OPEN,
        TradeIntent.SELL_TO_OPEN,
        TradeIntent.BUY_TO_CLOSE,
    ],
)
def test_long_open_rejects_wrong_transition(
    tmp_path,
    invalid_intent: TradeIntent,
) -> None:
    """Long-open signal permits only SELL_TO_CLOSE."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    result = guard.process(
        create_request(
            event_id="bad-event",
            intent=invalid_intent,
        )
    )

    assert result.allowed is False

    assert (
        guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.LONG_OPEN
    )


@pytest.mark.parametrize(
    "invalid_intent",
    [
        TradeIntent.BUY_TO_OPEN,
        TradeIntent.SELL_TO_OPEN,
        TradeIntent.SELL_TO_CLOSE,
    ],
)
def test_short_open_rejects_wrong_transition(
    tmp_path,
    invalid_intent: TradeIntent,
) -> None:
    """Short-open signal permits only BUY_TO_CLOSE."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    result = guard.process(
        create_request(
            event_id="bad-event",
            intent=invalid_intent,
        )
    )

    assert result.allowed is False

    assert (
        guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.SHORT_OPEN
    )


@pytest.mark.parametrize(
    "intent",
    list(
        TradeIntent
    ),
)
def test_closed_signal_rejects_every_future_intent(
    tmp_path,
    intent: TradeIntent,
) -> None:
    """Closed Eagle signal ID may never be reused."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    result = guard.process(
        create_request(
            event_id="after-close",
            intent=intent,
        )
    )

    assert result.allowed is False

    assert (
        guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_second_buy_to_open_is_rejected(
    tmp_path,
) -> None:
    """Same signal cannot open a second long position."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    result = guard.process(
        create_request(
            event_id="entry-002",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    assert result.allowed is False

    snapshot = guard.get_snapshot(
        "signal-001"
    )

    assert snapshot is not None
    assert (
        snapshot.state
        is SignalLifecycleState.LONG_OPEN
    )
    assert (
        snapshot.last_event_id
        == "entry-001"
    )


def test_second_sell_to_open_is_rejected(
    tmp_path,
) -> None:
    """Same signal cannot open a second short position."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    result = guard.process(
        create_request(
            event_id="entry-002",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    assert result.allowed is False


def test_second_close_is_rejected(
    tmp_path,
) -> None:
    """Already-closed signal cannot close twice."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    result = guard.process(
        create_request(
            event_id="exit-002",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    assert result.allowed is False

    snapshot = guard.get_snapshot(
        "signal-001"
    )

    assert snapshot is not None
    assert (
        snapshot.last_event_id
        == "exit-001"
    )


def test_invalid_transition_does_not_change_last_event(
    tmp_path,
) -> None:
    """Rejected transition must not mutate durable audit state."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-good",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="entry-bad",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    snapshot = guard.get_snapshot(
        "signal-001"
    )

    assert snapshot is not None
    assert snapshot.last_event_id == "entry-good"


def test_lifecycle_survives_restart(
    tmp_path,
) -> None:
    """Signal state must survive BTS process restart."""

    database_path = (
        tmp_path
        / "signal_lifecycle.db"
    )

    first_guard = SignalLifecycleGuard(
        database_path
    )

    first_guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    restarted_guard = SignalLifecycleGuard(
        database_path
    )

    assert (
        restarted_guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.LONG_OPEN
    )

    result = restarted_guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    assert result.allowed is True

    assert (
        restarted_guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_closed_state_survives_restart(
    tmp_path,
) -> None:
    """Closed signal must remain permanently closed after restart."""

    database_path = (
        tmp_path
        / "signal_lifecycle.db"
    )

    guard = SignalLifecycleGuard(
        database_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    restarted_guard = SignalLifecycleGuard(
        database_path
    )

    result = restarted_guard.process(
        create_request(
            event_id="reopen-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    assert result.allowed is False

    assert (
        restarted_guard.get_state(
            "signal-001"
        )
        is SignalLifecycleState.CLOSED
    )


def test_different_signals_are_independent(
    tmp_path,
) -> None:
    """One signal lifecycle must not mutate another."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="long-entry",
            signal_id="signal-long",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="short-entry",
            signal_id="signal-short",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    assert (
        guard.get_state(
            "signal-long"
        )
        is SignalLifecycleState.LONG_OPEN
    )

    assert (
        guard.get_state(
            "signal-short"
        )
        is SignalLifecycleState.SHORT_OPEN
    )


def test_all_snapshots_are_sorted(
    tmp_path,
) -> None:
    """Snapshot collection should be deterministic."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="event-b",
            signal_id="signal-b",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="event-a",
            signal_id="signal-a",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    snapshots = (
        guard.all_snapshots()
    )

    assert tuple(
        snapshot.signal_id
        for snapshot in snapshots
    ) == (
        "signal-a",
        "signal-b",
    )


def test_snapshot_contains_last_accepted_event(
    tmp_path,
) -> None:
    """Snapshot should preserve latest accepted transition."""

    guard = create_guard(
        tmp_path
    )

    guard.process(
        create_request(
            event_id="entry-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    guard.process(
        create_request(
            event_id="exit-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )
    )

    snapshot = guard.get_snapshot(
        "signal-001"
    )

    assert snapshot is not None
    assert (
        snapshot.state
        is SignalLifecycleState.CLOSED
    )
    assert snapshot.last_event_id == "exit-001"


def test_unknown_signal_returns_none(
    tmp_path,
) -> None:
    """Unknown signal should have no lifecycle state."""

    guard = create_guard(
        tmp_path
    )

    assert (
        guard.get_state(
            "unknown-signal"
        )
        is None
    )

    assert (
        guard.get_snapshot(
            "unknown-signal"
        )
        is None
    )


def test_invalid_trade_request_type_is_rejected(
    tmp_path,
) -> None:
    """Guard requires a TradeRequest."""

    guard = create_guard(
        tmp_path
    )

    with pytest.raises(
        TypeError,
        match="'trade_request'",
    ):
        guard.process(
            object()  # type: ignore[arg-type]
        )


def test_decision_is_immutable() -> None:
    """Lifecycle decisions must remain immutable."""

    decision = SignalLifecycleDecision(
        status=SignalLifecycleStatus.ACCEPTED,
        signal_id="signal-001",
        event_id="event-001",
        intent=TradeIntent.BUY_TO_OPEN,
        previous_state=None,
        next_state=SignalLifecycleState.LONG_OPEN,
        reason="Accepted.",
    )

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        decision.reason = "changed"  # type: ignore[misc]


def test_accepted_decision_reports_allowed() -> None:
    """Accepted transition should expose allowed=True."""

    decision = SignalLifecycleDecision(
        status=SignalLifecycleStatus.ACCEPTED,
        signal_id="signal-001",
        event_id="event-001",
        intent=TradeIntent.BUY_TO_OPEN,
        previous_state=None,
        next_state=SignalLifecycleState.LONG_OPEN,
        reason="Accepted.",
    )

    assert decision.allowed is True


def test_rejected_decision_reports_not_allowed() -> None:
    """Invalid transition should expose allowed=False."""

    decision = SignalLifecycleDecision(
        status=SignalLifecycleStatus.INVALID_TRANSITION,
        signal_id="signal-001",
        event_id="event-001",
        intent=TradeIntent.SELL_TO_CLOSE,
        previous_state=None,
        next_state=None,
        reason="Rejected.",
    )

    assert decision.allowed is False