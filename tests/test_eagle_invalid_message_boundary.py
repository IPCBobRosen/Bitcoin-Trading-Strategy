"""End-to-end invalid Eagle message boundary tests.

These tests prove malformed or invalid Eagle input fails closed
before it can become an approved BTS trade decision.

The boundary exercised here is:

    raw Eagle JSON
        ↓
    EagleClient parser
        ↓
    IncomingLifecycleEvent validation
        ↓
    TradeRequest construction
        ↓
    SignalLifecycleGuard
        ↓
    TradeCoordinator approval

No IB or broker execution code is used.
"""

import json
from pathlib import Path

import pytest

from app.communications.eagle_client import EagleClient
from app.communications.incoming_event import IncomingLifecycleEvent
from app.signal_lifecycle_guard import SignalLifecycleGuard
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


def create_client() -> EagleClient:
    """Create an offline Eagle parser."""

    return EagleClient(
        "ws://localhost:8765"
    )


def create_valid_message(
    *,
    message_type: str = "fund.entry",
    seq: int = 1,
    event_id: str = "event-001",
    signal_id: str = "signal-001",
    timestamp: str = "2026-08-16T20:00:00+00:00",
    environment: str = "staging",
    payload: object | None = None,
) -> dict[str, object]:
    """Create one valid lifecycle message dictionary."""

    if payload is None:
        payload = {
            "intent": "BUY_TO_OPEN",
        }

    return {
        "type": message_type,
        "seq": seq,
        "event_id": event_id,
        "signal_id": signal_id,
        "ts": timestamp,
        "env": environment,
        "payload": payload,
    }


def create_coordinator(
    tmp_path: Path,
) -> TradeCoordinator:
    """Create an enabled coordinator with durable lifecycle guard."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    controls.resume()

    lifecycle_guard = SignalLifecycleGuard(
        tmp_path
        / "signal_lifecycle.db"
    )

    return TradeCoordinator(
        controls,
        lifecycle_guard,
    )


def parse_message(
    message: dict[str, object],
) -> IncomingLifecycleEvent:
    """Parse and require a lifecycle event."""

    parsed = create_client()._parse_message(
        json.dumps(
            message
        )
    )

    if not isinstance(
        parsed,
        IncomingLifecycleEvent,
    ):
        raise RuntimeError(
            "Expected IncomingLifecycleEvent."
        )

    return parsed


def test_valid_buy_to_open_reaches_approved_trade_decision(
    tmp_path,
) -> None:
    """Control case: valid Eagle entry should pass the boundary."""

    coordinator = create_coordinator(
        tmp_path
    )

    event = parse_message(
        create_valid_message()
    )

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is True
    assert decision.trade_request is not None
    assert (
        decision.trade_request.intent.value
        == "BUY_TO_OPEN"
    )


def test_invalid_json_is_rejected_before_event_creation() -> None:
    """Malformed JSON must fail at the protocol boundary."""

    client = create_client()

    with pytest.raises(
        ValueError,
        match="valid JSON",
    ):
        client._parse_message(
            '{"type": "fund.entry",'
        )


@pytest.mark.parametrize(
    "raw_message",
    [
        "[]",
        '"fund.entry"',
        "123",
        "true",
        "null",
    ],
)
def test_non_object_json_is_rejected(
    raw_message: str,
) -> None:
    """Eagle JSON must decode to an object."""

    with pytest.raises(
        ValueError,
        match="JSON object",
    ):
        create_client()._parse_message(
            raw_message
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "seq",
        "event_id",
        "signal_id",
        "ts",
        "env",
    ],
)
def test_missing_required_lifecycle_field_is_rejected(
    missing_field: str,
) -> None:
    """Required Eagle envelope fields must be present."""

    message = create_valid_message()

    del message[
        missing_field
    ]

    with pytest.raises(
        ValueError,
        match="Missing required field",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "invalid_seq",
    [
        -1,
        True,
        False,
        1.5,
        "1",
        None,
    ],
)
def test_invalid_sequence_is_rejected(
    invalid_seq: object,
) -> None:
    """Sequence must be a non-negative integer."""

    message = create_valid_message()
    message["seq"] = invalid_seq

    with pytest.raises(
        ValueError,
        match="'seq'",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "invalid_event_id",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_event_id_is_rejected(
    invalid_event_id: object,
) -> None:
    """Event identity must contain text."""

    message = create_valid_message()
    message["event_id"] = invalid_event_id

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "invalid_signal_id",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_signal_id_is_rejected(
    invalid_signal_id: object,
) -> None:
    """Signal identity must contain text."""

    message = create_valid_message()
    message["signal_id"] = invalid_signal_id

    with pytest.raises(
        ValueError,
        match="'signal_id'",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "invalid_timestamp",
    [
        "",
        "not-a-timestamp",
        "2026-99-99",
        123,
        None,
    ],
)
def test_invalid_timestamp_is_rejected(
    invalid_timestamp: object,
) -> None:
    """Timestamp must be valid ISO-8601 text."""

    message = create_valid_message()
    message["ts"] = invalid_timestamp

    with pytest.raises(
        ValueError,
        match="'ts'",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "invalid_environment",
    [
        "",
        "paper",
        "production",
        "STAGING",
        None,
        123,
    ],
)
def test_invalid_environment_is_rejected(
    invalid_environment: object,
) -> None:
    """Only staging or live Eagle environments are valid."""

    message = create_valid_message()
    message["env"] = invalid_environment

    with pytest.raises(
        ValueError,
        match="'env'",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "invalid_payload",
    [
        [],
        "BUY_TO_OPEN",
        123,
        True,
    ],
)
def test_non_object_payload_is_rejected(
    invalid_payload: object,
) -> None:
    """Lifecycle payload must be a JSON object."""

    message = create_valid_message(
        payload=invalid_payload
    )

    with pytest.raises(
        ValueError,
        match="'payload'",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


@pytest.mark.parametrize(
    "unsupported_type",
    [
        "fund.add",
        "fund.unknown",
        "trade.lifecycle",
        "fund.lifecycle",
        "",
    ],
)
def test_unsupported_message_type_is_rejected(
    unsupported_type: str,
) -> None:
    """Unknown protocol types must fail before lifecycle processing."""

    message = create_valid_message(
        message_type=unsupported_type
    )

    with pytest.raises(
        ValueError,
        match="Unsupported Eagle message type",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


def test_missing_type_is_rejected() -> None:
    """Trade-shaped frame without type must fail closed."""

    message = create_valid_message()

    del message[
        "type"
    ]

    with pytest.raises(
        ValueError,
        match="Unsupported Eagle message type",
    ):
        create_client()._parse_message(
            json.dumps(
                message
            )
        )


def test_missing_intent_cannot_become_approved_trade(
    tmp_path,
) -> None:
    """Payload without intent must fail before approval."""

    coordinator = create_coordinator(
        tmp_path
    )

    event = parse_message(
        create_valid_message(
            payload={}
        )
    )

    with pytest.raises(
        (ValueError, TypeError),
    ):
        coordinator.process_event(
            event
        )


@pytest.mark.parametrize(
    "invalid_intent",
    [
        "",
        "BUY",
        "SELL",
        "HOLD",
        "buy_to_open",
        "CLOSE",
        None,
        123,
    ],
)
def test_invalid_trade_intent_cannot_become_approved_trade(
    tmp_path,
    invalid_intent: object,
) -> None:
    """Unsupported trade intent must fail before trade approval."""

    coordinator = create_coordinator(
        tmp_path
    )

    event = parse_message(
        create_valid_message(
            payload={
                "intent": invalid_intent,
            }
        )
    )

    with pytest.raises(
        (ValueError, TypeError),
    ):
        coordinator.process_event(
            event
        )


def test_close_before_entry_is_parsed_but_not_approved(
    tmp_path,
) -> None:
    """Structurally valid exit must still obey lifecycle rules."""

    coordinator = create_coordinator(
        tmp_path
    )

    event = parse_message(
        create_valid_message(
            message_type="fund.exit",
            event_id="exit-001",
            payload={
                "intent": "SELL_TO_CLOSE",
            },
        )
    )

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is False
    assert decision.trade_request is None
    assert (
        decision.reason
        == "InvalidSignalLifecycle"
    )


def test_valid_long_entry_then_valid_exit_is_approved(
    tmp_path,
) -> None:
    """Control lifecycle should still work through full boundary."""

    coordinator = create_coordinator(
        tmp_path
    )

    entry_event = parse_message(
        create_valid_message(
            message_type="fund.entry",
            seq=1,
            event_id="entry-001",
            signal_id="signal-001",
            payload={
                "intent": "BUY_TO_OPEN",
            },
        )
    )

    entry_decision = (
        coordinator.process_event(
            entry_event
        )
    )

    assert entry_decision.approved is True

    exit_event = parse_message(
        create_valid_message(
            message_type="fund.exit",
            seq=2,
            event_id="exit-001",
            signal_id="signal-001",
            payload={
                "intent": "SELL_TO_CLOSE",
            },
        )
    )

    exit_decision = (
        coordinator.process_event(
            exit_event
        )
    )

    assert exit_decision.approved is True
    assert exit_decision.trade_request is not None
    assert (
        exit_decision.trade_request.intent.value
        == "SELL_TO_CLOSE"
    )


def test_rejected_close_does_not_create_lifecycle_state(
    tmp_path,
) -> None:
    """Invalid exit must not mutate durable signal state."""

    coordinator = create_coordinator(
        tmp_path
    )

    event = parse_message(
        create_valid_message(
            message_type="fund.exit",
            event_id="bad-exit-001",
            signal_id="signal-001",
            payload={
                "intent": "SELL_TO_CLOSE",
            },
        )
    )

    decision = coordinator.process_event(
        event
    )

    assert decision.approved is False

    assert (
        coordinator.signal_lifecycle_guard.get_state(
            "signal-001"
        )
        is None
    )


def test_protocol_rejection_occurs_before_coordinator(
    tmp_path,
) -> None:
    """Malformed frame must never produce an event for coordinator."""

    coordinator = create_coordinator(
        tmp_path
    )

    message = create_valid_message()
    message["seq"] = -1

    event_created = False

    try:
        parsed = create_client()._parse_message(
            json.dumps(
                message
            )
        )

        event_created = isinstance(
            parsed,
            IncomingLifecycleEvent,
        )

    except ValueError:
        pass

    assert event_created is False

    assert (
        coordinator.signal_lifecycle_guard.all_snapshots()
        == ()
    )