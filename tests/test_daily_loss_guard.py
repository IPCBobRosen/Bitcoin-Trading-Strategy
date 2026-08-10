"""Tests for the BTS daily-loss guard."""

from decimal import Decimal

import pytest

from app.daily_loss_guard import (
    DailyLossGuard,
    DailyLossSnapshot,
)


def test_new_guard_starts_with_zero_pnl() -> None:
    """New daily guard should begin with no P&L."""

    guard = DailyLossGuard(
        5000
    )

    assert guard.realized_pnl == Decimal("0")
    assert guard.unrealized_pnl == Decimal("0")
    assert guard.total_pnl == Decimal("0")


def test_new_guard_is_not_tripped() -> None:
    """New daily guard should permit normal risk evaluation."""

    guard = DailyLossGuard(
        5000
    )

    assert guard.tripped is False


def test_max_daily_loss_is_normalized_to_decimal() -> None:
    """Configured loss limit should use Decimal internally."""

    guard = DailyLossGuard(
        "5000.50"
    )

    assert (
        guard.max_daily_loss
        == Decimal("5000.50")
    )


def test_realized_pnl_updates() -> None:
    """Realized P&L should be replaceable."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_realized_pnl(
        -1250
    )

    assert (
        guard.realized_pnl
        == Decimal("-1250")
    )


def test_unrealized_pnl_updates() -> None:
    """Unrealized P&L should be replaceable."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_unrealized_pnl(
        "-750.25"
    )

    assert (
        guard.unrealized_pnl
        == Decimal("-750.25")
    )


def test_total_pnl_combines_realized_and_unrealized() -> None:
    """Total daily P&L should combine both components."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-1000,
        unrealized_pnl=-500,
    )

    assert (
        guard.total_pnl
        == Decimal("-1500")
    )


def test_profit_does_not_trip_guard() -> None:
    """Positive daily P&L must not activate the guard."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=2500,
        unrealized_pnl=500,
    )

    assert guard.tripped is False


def test_loss_below_limit_does_not_trip_guard() -> None:
    """Loss smaller than configured limit should remain allowed."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-3000,
        unrealized_pnl=-1999,
    )

    assert guard.total_pnl == Decimal("-4999")
    assert guard.tripped is False


def test_exact_daily_loss_limit_trips_guard() -> None:
    """Reaching the configured daily loss must trip the guard."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-3000,
        unrealized_pnl=-2000,
    )

    assert guard.total_pnl == Decimal("-5000")
    assert guard.tripped is True


def test_loss_beyond_limit_trips_guard() -> None:
    """Loss beyond the limit must trip the guard."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-4000,
        unrealized_pnl=-1500,
    )

    assert guard.total_pnl == Decimal("-5500")
    assert guard.tripped is True


def test_realized_loss_alone_can_trip_guard() -> None:
    """Realized losses alone may breach the daily threshold."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_realized_pnl(
        -5000
    )

    assert guard.tripped is True


def test_unrealized_loss_alone_can_trip_guard() -> None:
    """Unrealized losses alone may breach the daily threshold."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_unrealized_pnl(
        -5000
    )

    assert guard.tripped is True


def test_trip_state_is_sticky_after_pnl_recovers() -> None:
    """Loss breach should remain active after later P&L recovery."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-4000,
        unrealized_pnl=-1500,
    )

    assert guard.tripped is True

    guard.update_pnl(
        realized_pnl=-1000,
        unrealized_pnl=500,
    )

    assert guard.total_pnl == Decimal("-500")
    assert guard.tripped is True


def test_remaining_loss_capacity_starts_at_limit() -> None:
    """Full loss capacity should remain at the start of the day."""

    guard = DailyLossGuard(
        5000
    )

    assert (
        guard.remaining_loss_capacity
        == Decimal("5000")
    )


def test_remaining_loss_capacity_decreases_with_losses() -> None:
    """Losses should consume remaining daily risk capacity."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-1000,
        unrealized_pnl=-500,
    )

    assert (
        guard.remaining_loss_capacity
        == Decimal("3500")
    )


def test_remaining_loss_capacity_increases_with_profit() -> None:
    """Profits should increase distance from the loss threshold."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_realized_pnl(
        1000
    )

    assert (
        guard.remaining_loss_capacity
        == Decimal("6000")
    )


def test_remaining_loss_capacity_is_zero_after_trip() -> None:
    """Tripped guard should expose no remaining loss capacity."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_realized_pnl(
        -5000
    )

    assert (
        guard.remaining_loss_capacity
        == Decimal("0")
    )


def test_reset_day_clears_pnl_and_trip_state() -> None:
    """New trading day should start with a clean guard."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-3000,
        unrealized_pnl=-2500,
    )

    assert guard.tripped is True

    guard.reset_day()

    assert guard.realized_pnl == Decimal("0")
    assert guard.unrealized_pnl == Decimal("0")
    assert guard.total_pnl == Decimal("0")
    assert guard.tripped is False


def test_snapshot_reports_current_state() -> None:
    """Snapshot should preserve the complete daily-loss state."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-1000,
        unrealized_pnl=-250,
    )

    result = guard.snapshot()

    assert result == DailyLossSnapshot(
        max_daily_loss=Decimal("5000"),
        realized_pnl=Decimal("-1000"),
        unrealized_pnl=Decimal("-250"),
        total_pnl=Decimal("-1250"),
        tripped=False,
    )


def test_snapshot_is_immutable() -> None:
    """Daily-loss snapshots must not be mutable."""

    guard = DailyLossGuard(
        5000
    )

    result = guard.snapshot()

    with pytest.raises(
        AttributeError,
    ):
        result.tripped = True  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_limit",
    [
        0,
        -1,
        True,
        "invalid",
        "NaN",
        "Infinity",
    ],
)
def test_invalid_max_daily_loss_is_rejected(
    invalid_limit: object,
) -> None:
    """Daily loss limit must be a finite positive number."""

    with pytest.raises(
        ValueError,
        match="'max_daily_loss' must be a positive number",
    ):
        DailyLossGuard(
            invalid_limit  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "invalid_pnl",
    [
        True,
        "invalid",
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_invalid_realized_pnl_is_rejected(
    invalid_pnl: object,
) -> None:
    """Realized P&L must be finite and numeric."""

    guard = DailyLossGuard(
        5000
    )

    with pytest.raises(
        ValueError,
        match="'realized_pnl' must be a finite number",
    ):
        guard.update_realized_pnl(
            invalid_pnl  # type: ignore[arg-type]
        )


def test_atomic_update_does_not_partially_change_state() -> None:
    """Invalid combined update must not partially alter P&L."""

    guard = DailyLossGuard(
        5000
    )

    guard.update_pnl(
        realized_pnl=-100,
        unrealized_pnl=-200,
    )

    with pytest.raises(
        ValueError,
        match="'unrealized_pnl' must be a finite number",
    ):
        guard.update_pnl(
            realized_pnl=-999,
            unrealized_pnl="invalid",
        )

    assert guard.realized_pnl == Decimal("-100")
    assert guard.unrealized_pnl == Decimal("-200")
    assert guard.total_pnl == Decimal("-300")