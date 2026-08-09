"""Tests for the TradeCoordinator."""

from decimal import Decimal

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import Environment, TradeIntent
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


def create_test_event() -> IncomingLifecycleEvent:
    """Create a valid accepted lifecycle event for coordinator tests."""

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


def test_process_event_rejects_event_when_trading_is_paused() -> None:
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


def test_process_event_creates_request_when_trading_is_enabled() -> None:
    """An enabled trading system should create a correct TradeRequest."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    controls.resume()

    expected_settings = controls.create_snapshot()

    coordinator = TradeCoordinator(controls)
    event = create_test_event()

    decision = coordinator.process_event(event)

    assert decision.approved is True
    assert decision.reason == "Approved"
    assert decision.trade_request is not None

    request = decision.trade_request

    assert request.event_id == event.event_id
    assert request.signal_id == event.signal_id
    assert request.timestamp == event.timestamp
    assert request.environment == Environment.STAGING
    assert request.intent == TradeIntent.BUY_TO_OPEN

    assert request.symbol == expected_settings.symbol
    assert request.quantity == expected_settings.quantity
    assert request.stop_loss_points == Decimal("500")
    assert request.stop_loss_points == expected_settings.stop_loss_points