"""Tests for BTS duplicate-order protection."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.duplicate_order_guard import (
    DuplicateOrderDecision,
    DuplicateOrderGuard,
    DuplicateOrderSnapshot,
    DuplicateOrderStatus,
)


def create_trade_request(
    *,
    event_id: str = "event-001",
    signal_id: str = "signal-001",
    intent_value: str = "BUY_TO_OPEN",
) -> TradeRequest:
    """Create a deterministic TradeRequest for duplicate tests."""

    return TradeRequest(
        event_id=event_id,
        signal_id=signal_id,
        timestamp=datetime(
            2026,
            8,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent(
            intent_value
        ),
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )


def test_new_guard_has_no_reservations() -> None:
    """New duplicate guard should begin empty."""

    guard = DuplicateOrderGuard()

    assert guard.reservation_count == 0


def test_first_event_reservation_is_allowed() -> None:
    """First reservation for an Eagle event should succeed."""

    guard = DuplicateOrderGuard()

    result = guard.reserve(
        create_trade_request()
    )

    assert (
        result.status
        is DuplicateOrderStatus.RESERVED
    )

    assert result.allowed is True
    assert guard.reservation_count == 1


def test_reserved_event_is_contained() -> None:
    """Guard should remember a successfully reserved event."""

    guard = DuplicateOrderGuard()

    guard.reserve(
        create_trade_request(
            event_id="event-123"
        )
    )

    assert (
        guard.contains(
            "event-123"
        )
        is True
    )


def test_duplicate_event_is_rejected() -> None:
    """Second reservation of the same event ID must fail."""

    guard = DuplicateOrderGuard()

    trade_request = (
        create_trade_request()
    )

    guard.reserve(
        trade_request
    )

    result = guard.reserve(
        trade_request
    )

    assert (
        result.status
        is DuplicateOrderStatus.DUPLICATE
    )

    assert result.allowed is False
    assert guard.reservation_count == 1


def test_duplicate_event_reason_is_explanatory() -> None:
    """Duplicate rejection should identify the reserved event."""

    guard = DuplicateOrderGuard()

    guard.reserve(
        create_trade_request(
            event_id="event-777"
        )
    )

    result = guard.reserve(
        create_trade_request(
            event_id="event-777"
        )
    )

    assert "already" in result.reason.lower()
    assert "event-777" in result.reason


def test_different_trade_request_object_same_event_is_duplicate() -> None:
    """Idempotency must depend on event ID, not object identity."""

    guard = DuplicateOrderGuard()

    first = create_trade_request(
        event_id="event-001",
        signal_id="signal-001",
    )

    second = create_trade_request(
        event_id="event-001",
        signal_id="signal-001",
    )

    assert first is not second

    guard.reserve(
        first
    )

    result = guard.reserve(
        second
    )

    assert (
        result.status
        is DuplicateOrderStatus.DUPLICATE
    )


def test_different_events_are_allowed() -> None:
    """Distinct Eagle events should each receive reservations."""

    guard = DuplicateOrderGuard()

    first_result = guard.reserve(
        create_trade_request(
            event_id="event-001"
        )
    )

    second_result = guard.reserve(
        create_trade_request(
            event_id="event-002"
        )
    )

    assert first_result.allowed is True
    assert second_result.allowed is True
    assert guard.reservation_count == 2


def test_same_signal_id_with_different_events_is_allowed() -> None:
    """One signal may legitimately contain distinct lifecycle events."""

    guard = DuplicateOrderGuard()

    entry = create_trade_request(
        event_id="entry-event-001",
        signal_id="signal-001",
        intent_value="BUY_TO_OPEN",
    )

    exit_request = create_trade_request(
        event_id="exit-event-001",
        signal_id="signal-001",
        intent_value="SELL_TO_CLOSE",
    )

    entry_result = guard.reserve(
        entry
    )

    exit_result = guard.reserve(
        exit_request
    )

    assert entry_result.allowed is True
    assert exit_result.allowed is True
    assert guard.reservation_count == 2


def test_duplicate_event_remains_duplicate_with_different_signal_id() -> None:
    """Event ID remains authoritative even if signal metadata differs."""

    guard = DuplicateOrderGuard()

    guard.reserve(
        create_trade_request(
            event_id="event-001",
            signal_id="signal-001",
        )
    )

    result = guard.reserve(
        create_trade_request(
            event_id="event-001",
            signal_id="signal-999",
        )
    )

    assert (
        result.status
        is DuplicateOrderStatus.DUPLICATE
    )


def test_release_existing_reservation_returns_true() -> None:
    """Existing reservation should be releasable."""

    guard = DuplicateOrderGuard()

    guard.reserve(
        create_trade_request(
            event_id="event-001"
        )
    )

    result = guard.release(
        "event-001"
    )

    assert result is True
    assert guard.reservation_count == 0


def test_released_event_can_be_reserved_again() -> None:
    """A locally released event may be retried."""

    guard = DuplicateOrderGuard()

    trade_request = (
        create_trade_request(
            event_id="event-001"
        )
    )

    guard.reserve(
        trade_request
    )

    guard.release(
        "event-001"
    )

    result = guard.reserve(
        trade_request
    )

    assert result.allowed is True


def test_release_unknown_event_returns_false() -> None:
    """Releasing an event that is not reserved should be harmless."""

    guard = DuplicateOrderGuard()

    assert (
        guard.release(
            "unknown-event"
        )
        is False
    )


def test_clear_removes_all_reservations() -> None:
    """Clear should remove every tracked event."""

    guard = DuplicateOrderGuard()

    guard.reserve(
        create_trade_request(
            event_id="event-001"
        )
    )

    guard.reserve(
        create_trade_request(
            event_id="event-002"
        )
    )

    guard.clear()

    assert guard.reservation_count == 0
    assert guard.contains("event-001") is False
    assert guard.contains("event-002") is False


def test_snapshot_reports_reserved_events() -> None:
    """Snapshot should contain deterministic reserved IDs."""

    guard = DuplicateOrderGuard()

    guard.reserve(
        create_trade_request(
            event_id="event-002"
        )
    )

    guard.reserve(
        create_trade_request(
            event_id="event-001"
        )
    )

    result = guard.snapshot()

    assert result == DuplicateOrderSnapshot(
        reserved_event_ids=(
            "event-001",
            "event-002",
        ),
        reservation_count=2,
    )


def test_snapshot_is_immutable() -> None:
    """Duplicate-order snapshots must not be mutable."""

    guard = DuplicateOrderGuard()

    result = guard.snapshot()

    with pytest.raises(
        AttributeError,
    ):
        result.reservation_count = 10  # type: ignore[misc]


def test_decision_is_immutable() -> None:
    """Duplicate-order decisions must not be mutable."""

    decision = DuplicateOrderDecision(
        status=DuplicateOrderStatus.RESERVED,
        event_id="event-001",
        signal_id="signal-001",
        reason="Reserved.",
    )

    with pytest.raises(
        AttributeError,
    ):
        decision.reason = "Changed."  # type: ignore[misc]


def test_invalid_trade_request_is_rejected() -> None:
    """Reserve requires a TradeRequest."""

    guard = DuplicateOrderGuard()

    with pytest.raises(
        TypeError,
        match="'trade_request' must be a TradeRequest",
    ):
        guard.reserve(
            object()  # type: ignore[arg-type]
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
def test_invalid_contains_event_id_is_rejected(
    invalid_event_id: object,
) -> None:
    """contains requires a non-empty event ID."""

    guard = DuplicateOrderGuard()

    with pytest.raises(
        ValueError,
        match="'event_id' must be a non-empty string",
    ):
        guard.contains(
            invalid_event_id  # type: ignore[arg-type]
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
def test_invalid_release_event_id_is_rejected(
    invalid_event_id: object,
) -> None:
    """release requires a non-empty event ID."""

    guard = DuplicateOrderGuard()

    with pytest.raises(
        ValueError,
        match="'event_id' must be a non-empty string",
    ):
        guard.release(
            invalid_event_id  # type: ignore[arg-type]
        )