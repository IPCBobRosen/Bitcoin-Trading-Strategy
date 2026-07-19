"""Tests for trader-adjustable runtime controls."""

from decimal import Decimal

import pytest

from app.trading_controls import TradingControls


def test_controls_start_paused() -> None:
    """The trading system should start paused for safety."""

    controls = TradingControls()

    assert controls.is_paused is True


def test_controls_can_pause_and_resume() -> None:
    """The trader should be able to pause and resume the system."""

    controls = TradingControls()

    controls.resume()
    assert controls.is_paused is False

    controls.pause()
    assert controls.is_paused is True


def test_controls_use_expected_defaults() -> None:
    """Default settings should be safe and predictable."""

    controls = TradingControls()

    assert controls.symbol == "MBT"
    assert controls.quantity == 1
    assert controls.stop_loss_points == Decimal("500")


def test_controls_can_be_updated() -> None:
    """Updates should affect settings used for future requests."""

    controls = TradingControls()

    controls.update(
        symbol="mbtq26",
        quantity=10,
        stop_loss_points="750.5",
    )

    assert controls.symbol == "MBTQ26"
    assert controls.quantity == 10
    assert controls.stop_loss_points == Decimal("750.5")


def test_snapshot_is_not_changed_by_later_updates() -> None:
    """Existing snapshots should preserve their original settings."""

    controls = TradingControls(
        symbol="MBT",
        quantity=5,
        stop_loss_points=500,
    )

    first_snapshot = controls.create_snapshot()

    controls.update(
        symbol="MBTQ26",
        quantity=10,
        stop_loss_points=750,
    )

    second_snapshot = controls.create_snapshot()

    assert first_snapshot.symbol == "MBT"
    assert first_snapshot.quantity == 5
    assert first_snapshot.stop_loss_points == Decimal("500")

    assert second_snapshot.symbol == "MBTQ26"
    assert second_snapshot.quantity == 10
    assert second_snapshot.stop_loss_points == Decimal("750")


def test_controls_reject_invalid_quantity() -> None:
    """Zero or negative contract quantities should be rejected."""

    controls = TradingControls()

    with pytest.raises(
        ValueError,
        match="'quantity' must be a positive integer",
    ):
        controls.update(quantity=0)


def test_controls_reject_invalid_stop_loss() -> None:
    """A zero or negative stop-loss distance should be rejected."""

    controls = TradingControls()

    with pytest.raises(
        ValueError,
        match="'stop_loss_points' must be a positive number",
    ):
        controls.update(stop_loss_points=-100)


def test_failed_update_does_not_partially_change_settings() -> None:
    """An invalid update should leave all current settings unchanged."""

    controls = TradingControls(
        symbol="MBT",
        quantity=5,
        stop_loss_points=500,
    )

    with pytest.raises(ValueError):
        controls.update(
            symbol="MBTQ26",
            quantity=0,
            stop_loss_points=750,
        )

    assert controls.symbol == "MBT"
    assert controls.quantity == 5
    assert controls.stop_loss_points == Decimal("500")