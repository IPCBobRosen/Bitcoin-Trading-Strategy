"""Tests for the real-Eagle to BTS trade adapter."""

from pathlib import Path

import pytest

from app.communications.eagle_trade_adapter import (
    SUPPORTED_EAGLE_SYMBOL,
    EagleTradeAdaptStatus,
    EagleTradeAdapter,
)
from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import TradeIntent
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


def create_real_entry(
    *,
    symbol: str = "BTCUSDT",
    direction: str = "long",
    signal_id: str = "bt-test-BTCUSDT-0",
    event_id: str = "bt-test-BTCUSDT-0:entry",
    seq: int = 1,
) -> IncomingLifecycleEvent:
    """Create one real-Eagle-shaped entry event."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": signal_id,
            "ts": "2026-08-17T15:00:00.000Z",
            "env": "staging",
            "signal": {
                "direction": direction,
                "symbol": symbol,
                "size_mult": 1,
                "entry": 100000.0,
                "stop": 99500.0,
                "target": 101000.0,
                "play_id": "P11",
            },
        }
    )


def create_real_exit(
    *,
    signal_id: str = "bt-test-BTCUSDT-0",
    event_id: str = "bt-test-BTCUSDT-0:exit",
    seq: int = 2,
) -> IncomingLifecycleEvent:
    """Create one real-Eagle-shaped exit event."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.exit",
            "seq": seq,
            "event_id": event_id,
            "signal_id": signal_id,
            "ts": "2026-08-17T15:05:00.000Z",
            "env": "staging",
            "closed_at": "2026-08-17T15:05:00.000Z",
            "exit_price": 101000.0,
            "outcome": "trail",
            "realized_r": 1.5,
        }
    )


def create_coordinator(
    lifecycle_guard: SignalLifecycleGuard,
) -> TradeCoordinator:
    """Create a production TradeCoordinator for adapter tests."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    controls.resume()

    return TradeCoordinator(
        controls=controls,
        signal_lifecycle_guard=lifecycle_guard,
    )


def test_supported_symbol_is_btcusdt() -> None:
    """Adapter should currently allow Bitcoin only."""

    assert SUPPORTED_EAGLE_SYMBOL == "BTCUSDT"


def test_constructor_requires_lifecycle_guard() -> None:
    """Adapter must use durable signal lifecycle state."""

    with pytest.raises(
        TypeError,
        match="'lifecycle_guard' must be a SignalLifecycleGuard",
    ):
        EagleTradeAdapter(
            object(),  # type: ignore[arg-type]
        )


def test_long_btc_entry_maps_to_buy_to_open(
    tmp_path: Path,
) -> None:
    """BTC long entry should become BUY_TO_OPEN."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    result = adapter.adapt(
        create_real_entry(
            direction="long",
        )
    )

    assert result.adapted is True

    assert (
        result.status
        is EagleTradeAdaptStatus.ADAPTED
    )

    assert result.eagle_symbol == "BTCUSDT"
    assert result.event is not None

    assert (
        result.event.payload["intent"]
        == TradeIntent.BUY_TO_OPEN.value
    )


def test_short_btc_entry_maps_to_sell_to_open(
    tmp_path: Path,
) -> None:
    """BTC short entry should become SELL_TO_OPEN."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    result = adapter.adapt(
        create_real_entry(
            direction="short",
        )
    )

    assert result.adapted is True
    assert result.event is not None

    assert (
        result.event.payload["intent"]
        == TradeIntent.SELL_TO_OPEN.value
    )


def test_adapter_preserves_signal_data(
    tmp_path: Path,
) -> None:
    """Normalization should preserve Eagle signal metadata."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    result = adapter.adapt(
        create_real_entry()
    )

    assert result.event is not None

    signal = result.event.payload["signal"]

    assert signal["symbol"] == "BTCUSDT"
    assert signal["direction"] == "long"
    assert signal["size_mult"] == 1
    assert signal["play_id"] == "P11"


def test_eth_entry_is_ignored(
    tmp_path: Path,
) -> None:
    """ETH must not enter the BTC/MBT decision path."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    result = adapter.adapt(
        create_real_entry(
            symbol="ETHUSDT",
            signal_id="bt-test-ETHUSDT-0",
            event_id="bt-test-ETHUSDT-0:entry",
        )
    )

    assert result.adapted is False

    assert (
        result.status
        is EagleTradeAdaptStatus.IGNORED_SYMBOL
    )

    assert result.event is None
    assert result.eagle_symbol == "ETHUSDT"


def test_case_insensitive_symbol_is_normalized(
    tmp_path: Path,
) -> None:
    """BTC symbol comparison should be case-insensitive."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    result = adapter.adapt(
        create_real_entry(
            symbol="btcusdt",
        )
    )

    assert result.adapted is True
    assert result.eagle_symbol == "BTCUSDT"


def test_missing_signal_object_is_rejected(
    tmp_path: Path,
) -> None:
    """fund.entry must include Eagle signal metadata."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    event = IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": 1,
            "event_id": "event-001",
            "signal_id": "signal-001",
            "ts": "2026-08-17T15:00:00Z",
            "env": "staging",
        }
    )

    with pytest.raises(
        ValueError,
        match="fund.entry must contain a signal object",
    ):
        adapter.adapt(
            event
        )


def test_missing_symbol_is_rejected(
    tmp_path: Path,
) -> None:
    """Entry signal must contain an explicit symbol."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    event = IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": 1,
            "event_id": "event-001",
            "signal_id": "signal-001",
            "ts": "2026-08-17T15:00:00Z",
            "env": "staging",
            "signal": {
                "direction": "long",
            },
        }
    )

    with pytest.raises(
        ValueError,
        match="must contain a non-empty 'symbol'",
    ):
        adapter.adapt(
            event
        )


def test_missing_direction_is_rejected(
    tmp_path: Path,
) -> None:
    """Entry signal must contain an explicit direction."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    event = IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": 1,
            "event_id": "event-001",
            "signal_id": "signal-001",
            "ts": "2026-08-17T15:00:00Z",
            "env": "staging",
            "signal": {
                "symbol": "BTCUSDT",
            },
        }
    )

    with pytest.raises(
        ValueError,
        match="must contain a non-empty 'direction'",
    ):
        adapter.adapt(
            event
        )


def test_unknown_direction_is_rejected(
    tmp_path: Path,
) -> None:
    """Unexpected Eagle direction must fail closed."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    with pytest.raises(
        ValueError,
        match="Unsupported Eagle entry direction",
    ):
        adapter.adapt(
            create_real_entry(
                direction="sideways",
            )
        )


def test_long_exit_maps_to_sell_to_close(
    tmp_path: Path,
) -> None:
    """Exit from LONG_OPEN should become SELL_TO_CLOSE."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    coordinator = create_coordinator(
        guard
    )

    adapter = EagleTradeAdapter(
        guard
    )

    entry_result = adapter.adapt(
        create_real_entry(
            direction="long",
        )
    )

    assert entry_result.event is not None

    entry_decision = coordinator.process_event(
        entry_result.event
    )

    assert entry_decision.approved is True

    assert (
        guard.get_state(
            "bt-test-BTCUSDT-0"
        )
        is SignalLifecycleState.LONG_OPEN
    )

    exit_result = adapter.adapt(
        create_real_exit()
    )

    assert exit_result.adapted is True
    assert exit_result.event is not None

    assert (
        exit_result.event.payload["intent"]
        == TradeIntent.SELL_TO_CLOSE.value
    )


def test_short_exit_maps_to_buy_to_close(
    tmp_path: Path,
) -> None:
    """Exit from SHORT_OPEN should become BUY_TO_CLOSE."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    coordinator = create_coordinator(
        guard
    )

    adapter = EagleTradeAdapter(
        guard
    )

    entry_result = adapter.adapt(
        create_real_entry(
            direction="short",
        )
    )

    assert entry_result.event is not None

    entry_decision = coordinator.process_event(
        entry_result.event
    )

    assert entry_decision.approved is True

    assert (
        guard.get_state(
            "bt-test-BTCUSDT-0"
        )
        is SignalLifecycleState.SHORT_OPEN
    )

    exit_result = adapter.adapt(
        create_real_exit()
    )

    assert exit_result.adapted is True
    assert exit_result.event is not None

    assert (
        exit_result.event.payload["intent"]
        == TradeIntent.BUY_TO_CLOSE.value
    )


def test_unknown_exit_is_ignored(
    tmp_path: Path,
) -> None:
    """Exit with no known BTS lifecycle must not become MBT close."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    result = adapter.adapt(
        create_real_exit(
            signal_id="unknown-signal",
            event_id="unknown-signal:exit",
        )
    )

    assert result.adapted is False

    assert (
        result.status
        is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
    )

    assert result.event is None


def test_eth_entry_then_exit_both_stay_out_of_btc_path(
    tmp_path: Path,
) -> None:
    """Ignored ETH entry should leave no lifecycle for later exit."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    eth_signal_id = "bt-test-ETHUSDT-0"

    entry_result = adapter.adapt(
        create_real_entry(
            symbol="ETHUSDT",
            signal_id=eth_signal_id,
            event_id=f"{eth_signal_id}:entry",
        )
    )

    assert (
        entry_result.status
        is EagleTradeAdaptStatus.IGNORED_SYMBOL
    )

    assert (
        guard.get_state(
            eth_signal_id
        )
        is None
    )

    exit_result = adapter.adapt(
        create_real_exit(
            signal_id=eth_signal_id,
            event_id=f"{eth_signal_id}:exit",
        )
    )

    assert (
        exit_result.status
        is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
    )

    assert exit_result.event is None


def test_closed_signal_exit_is_ignored(
    tmp_path: Path,
) -> None:
    """Additional exit after CLOSED must not create another close."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    coordinator = create_coordinator(
        guard
    )

    adapter = EagleTradeAdapter(
        guard
    )

    entry_result = adapter.adapt(
        create_real_entry(
            direction="long",
        )
    )

    assert entry_result.event is not None

    assert (
        coordinator.process_event(
            entry_result.event
        ).approved
        is True
    )

    first_exit = adapter.adapt(
        create_real_exit(
            seq=2,
            event_id="bt-test-BTCUSDT-0:exit-1",
        )
    )

    assert first_exit.event is not None

    assert (
        coordinator.process_event(
            first_exit.event
        ).approved
        is True
    )

    assert (
        guard.get_state(
            "bt-test-BTCUSDT-0"
        )
        is SignalLifecycleState.CLOSED
    )

    second_exit = adapter.adapt(
        create_real_exit(
            seq=3,
            event_id="bt-test-BTCUSDT-0:exit-2",
        )
    )

    assert second_exit.adapted is False

    assert (
        second_exit.status
        is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
    )


def test_unsupported_message_type_is_rejected(
    tmp_path: Path,
) -> None:
    """Adapter should accept only entry/exit lifecycle frames."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    adapter = EagleTradeAdapter(
        guard
    )

    event = IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.update",
            "seq": 10,
            "event_id": "event-update",
            "signal_id": "signal-update",
            "ts": "2026-08-17T15:00:00Z",
            "env": "staging",
        }
    )

    with pytest.raises(
        ValueError,
        match="Unsupported Eagle lifecycle message type",
    ):
        adapter.adapt(
            event
        )

def test_rejected_second_entry_exit_does_not_close_executed_signal(
    tmp_path: Path,
) -> None:
    """Exit for unexecuted Signal B must not close executed Signal A."""

    guard = SignalLifecycleGuard(
        tmp_path / "signals.db"
    )

    coordinator = create_coordinator(
        guard
    )

    adapter = EagleTradeAdapter(
        guard
    )

    signal_a = "signal-a-executed"
    signal_b = "signal-b-rejected"

    # Signal A is the entry BTS actually accepts.
    entry_a = adapter.adapt(
        create_real_entry(
            direction="long",
            signal_id=signal_a,
            event_id=f"{signal_a}:entry",
            seq=1,
        )
    )

    assert entry_a.event is not None

    assert (
        coordinator.process_event(
            entry_a.event
        ).approved
        is True
    )

    assert (
        guard.get_state(signal_a)
        is SignalLifecycleState.LONG_OPEN
    )

    # Signal B arrives, but represents the second entry that BTS
    # does NOT commit because broker exposure is already at max.
    entry_b = adapter.adapt(
        create_real_entry(
            direction="long",
            signal_id=signal_b,
            event_id=f"{signal_b}:entry",
            seq=2,
        )
    )

    assert entry_b.adapted is True
    assert entry_b.event is not None

    # Simulate the LIVE runner rejecting Signal B BEFORE
    # coordinator.commit_request() is called.
    prepared_b = coordinator.prepare_event(
        entry_b.event
    )

    assert prepared_b.approved is True
    assert prepared_b.trade_request is not None

    # Signal B must have no executable lifecycle.
    assert guard.get_state(signal_b) is None

    # Eagle exits Signal B first.
    exit_b = adapter.adapt(
        create_real_exit(
            signal_id=signal_b,
            event_id=f"{signal_b}:exit",
            seq=3,
        )
    )

    assert exit_b.adapted is False

    assert (
        exit_b.status
        is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
    )

    assert exit_b.event is None

    # Most importantly, ignoring B's exit must not affect A.
    assert (
        guard.get_state(signal_a)
        is SignalLifecycleState.LONG_OPEN
    )

    # Eagle later exits Signal A.
    exit_a = adapter.adapt(
        create_real_exit(
            signal_id=signal_a,
            event_id=f"{signal_a}:exit",
            seq=4,
        )
    )

    assert exit_a.adapted is True
    assert exit_a.event is not None

    assert (
        exit_a.event.payload["intent"]
        == TradeIntent.SELL_TO_CLOSE.value
    )