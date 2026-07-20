"""Tests for the TradeCoordinator."""

from app.communications.incoming_event import IncomingLifecycleEvent
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


def create_test_event() -> IncomingLifecycleEvent:
    """Create a valid lifecycle event for coordinator tests."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": 1,
            "event_id": "test-event-001",
            "signal_id": "test-signal-001",
            "ts": "2026-07-20T12:00:00+00:00",
            "env": "staging",
            "payload": {
                "intent": "BUY_TO_OPEN",
            },
        }
    )


def test_process_event_returns_none_when_trading_is_paused() -> None:
    """A paused trading system must not create a TradeRequest."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )
    coordinator = TradeCoordinator(controls)
    event = create_test_event()

    decision = coordinator.process_event(event)

    assert decision.approved is False
    assert decision.reason == "TradingPaused"
    assert decision.trade_request is None