"""Tests for trade requests created from Eagle lifecycle events."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import Environment, TradeIntent
from app.communications.trade_request import TradeRequest
from app.trading_controls import TradingControls


def create_valid_event(
    intent: str = "BUY_TO_OPEN",
) -> IncomingLifecycleEvent:
    """Create a valid lifecycle event for trade-request tests."""

    message = {
        "type": "fund.entry",
        "seq": 1204,
        "event_id": "event-001",
        "signal_id": "signal-001",
        "ts": "2026-08-01T09:01:00+00:00",
        "env": "staging",
        "payload": {
            "intent": intent,
        },
    }

    return IncomingLifecycleEvent.from_dict(message)


def test_from_event_creates_trade_request() -> None:
    """A valid event and settings snapshot should create a request."""

    event = create_valid_event()

    controls = TradingControls(
        symbol="MBTQ26",
        quantity=10,
        stop_loss_points=750,
    )

    settings = controls.create_snapshot()

    request = TradeRequest.from_event(event, settings)

    assert request.event_id == "event-001"
    assert request.signal_id == "signal-001"
    assert request.environment is Environment.STAGING
    assert request.intent is TradeIntent.BUY_TO_OPEN
    assert request.symbol == "MBTQ26"
    assert request.quantity == 10
    assert request.stop_loss_points == Decimal("750")


def test_request_uses_trader_settings_snapshot() -> None:
    """The request should use BTS settings rather than an Eagle symbol."""

    message = {
        "type": "fund.entry",
        "seq": 1204,
        "event_id": "event-001",
        "signal_id": "signal-001",
        "ts": "2026-08-01T09:01:00+00:00",
        "env": "staging",
        "payload": {
            "intent": "SELL_TO_OPEN",
            "symbol": "A_SYMBOL_SENT_BY_EAGLE",
        },
    }

    event = IncomingLifecycleEvent.from_dict(message)

    controls = TradingControls(
        symbol="MBTV26",
        quantity=5,
        stop_loss_points=600,
    )

    request = TradeRequest.from_event(
        event,
        controls.create_snapshot(),
    )

    assert request.intent is TradeIntent.SELL_TO_OPEN
    assert request.symbol == "MBTV26"
    assert request.quantity == 5
    assert request.stop_loss_points == Decimal("600")


def test_request_is_not_changed_by_later_control_updates() -> None:
    """Later trader changes must not alter an existing request."""

    event = create_valid_event()

    controls = TradingControls(
        symbol="MBTQ26",
        quantity=5,
        stop_loss_points=500,
    )

    request = TradeRequest.from_event(
        event,
        controls.create_snapshot(),
    )

    controls.update(
        symbol="MBTV26",
        quantity=20,
        stop_loss_points=900,
    )

    assert request.symbol == "MBTQ26"
    assert request.quantity == 5
    assert request.stop_loss_points == Decimal("500")


def test_request_is_immutable() -> None:
    """A created trade request must not be editable."""

    event = create_valid_event()

    controls = TradingControls(
        symbol="MBTQ26",
        quantity=10,
        stop_loss_points=750,
    )

    request = TradeRequest.from_event(
        event,
        controls.create_snapshot(),
    )

    with pytest.raises(FrozenInstanceError):
        request.quantity = 100  # type: ignore[misc]


def test_from_event_rejects_missing_intent() -> None:
    """An event without an intent should not create a request."""

    message = {
        "type": "fund.entry",
        "seq": 1204,
        "event_id": "event-001",
        "signal_id": "signal-001",
        "ts": "2026-08-01T09:01:00+00:00",
        "env": "staging",
        "payload": {},
    }

    event = IncomingLifecycleEvent.from_dict(message)
    controls = TradingControls()

    with pytest.raises(
        ValueError,
        match="must contain a string 'intent'",
    ):
        TradeRequest.from_event(
            event,
            controls.create_snapshot(),
        )


def test_from_event_rejects_unsupported_intent() -> None:
    """An unknown trade intention should be rejected."""

    event = create_valid_event(intent="BUY_EVERYTHING")
    controls = TradingControls()

    with pytest.raises(
        ValueError,
        match="Unsupported trade intent",
    ):
        TradeRequest.from_event(
            event,
            controls.create_snapshot(),
        )