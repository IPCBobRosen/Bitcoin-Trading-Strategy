"""Tests for open-position reconciliation."""

import pytest

from app.reconciliation_manager import (
    ReconciliationManager,
    ReconciliationStatus,
)


def test_new_manager_is_not_checked() -> None:
    """A new manager should begin in NOT_CHECKED state."""

    manager = ReconciliationManager()

    result = manager.last_result

    assert result.status is ReconciliationStatus.NOT_CHECKED
    assert result.matched is False
    assert result.eagle_positions == ()
    assert result.broker_positions == ()


def test_empty_position_books_match() -> None:
    """Two empty position books should reconcile successfully."""

    manager = ReconciliationManager()

    result = manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True


def test_identical_position_books_match() -> None:
    """Identical Eagle and broker positions should match."""

    manager = ReconciliationManager()

    eagle_positions = [
        {
            "signal_id": "signal-001",
            "symbol": "MBT",
            "side": "LONG",
            "quantity": 1,
        }
    ]

    broker_positions = [
        {
            "signal_id": "signal-001",
            "symbol": "MBT",
            "side": "LONG",
            "quantity": 1,
        }
    ]

    result = manager.reconcile(
        eagle_positions=eagle_positions,
        broker_positions=broker_positions,
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True


def test_position_order_does_not_matter() -> None:
    """Equivalent position books should match regardless of order."""

    manager = ReconciliationManager()

    eagle_positions = [
        {
            "signal_id": "signal-001",
            "symbol": "MBT",
            "side": "LONG",
            "quantity": 1,
        },
        {
            "signal_id": "signal-002",
            "symbol": "MBT",
            "side": "SHORT",
            "quantity": 2,
        },
    ]

    broker_positions = [
        {
            "signal_id": "signal-002",
            "symbol": "MBT",
            "side": "SHORT",
            "quantity": 2,
        },
        {
            "signal_id": "signal-001",
            "symbol": "MBT",
            "side": "LONG",
            "quantity": 1,
        },
    ]

    result = manager.reconcile(
        eagle_positions=eagle_positions,
        broker_positions=broker_positions,
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True


def test_different_quantity_is_mismatch() -> None:
    """Different position quantities must fail reconciliation."""

    manager = ReconciliationManager()

    result = manager.reconcile(
        eagle_positions=[
            {
                "signal_id": "signal-001",
                "symbol": "MBT",
                "side": "LONG",
                "quantity": 1,
            }
        ],
        broker_positions=[
            {
                "signal_id": "signal-001",
                "symbol": "MBT",
                "side": "LONG",
                "quantity": 2,
            }
        ],
    )

    assert result.status is ReconciliationStatus.MISMATCHED
    assert result.matched is False


def test_missing_broker_position_is_mismatch() -> None:
    """A position known to Eagle but missing at broker must fail."""

    manager = ReconciliationManager()

    result = manager.reconcile(
        eagle_positions=[
            {
                "signal_id": "signal-001",
                "symbol": "MBT",
                "side": "LONG",
                "quantity": 1,
            }
        ],
        broker_positions=[],
    )

    assert result.status is ReconciliationStatus.MISMATCHED


def test_extra_broker_position_is_mismatch() -> None:
    """An unexpected broker position must fail reconciliation."""

    manager = ReconciliationManager()

    result = manager.reconcile(
        eagle_positions=[],
        broker_positions=[
            {
                "signal_id": "signal-999",
                "symbol": "MBT",
                "side": "LONG",
                "quantity": 1,
            }
        ],
    )

    assert result.status is ReconciliationStatus.MISMATCHED


def test_invalid_eagle_positions_type_is_rejected() -> None:
    """Eagle position collection must be a list or tuple."""

    manager = ReconciliationManager()

    with pytest.raises(
        TypeError,
        match="'eagle_positions' must be a list or tuple",
    ):
        manager.reconcile(
            eagle_positions="invalid",  # type: ignore[arg-type]
            broker_positions=[],
        )


def test_invalid_broker_positions_type_is_rejected() -> None:
    """Broker position collection must be a list or tuple."""

    manager = ReconciliationManager()

    with pytest.raises(
        TypeError,
        match="'broker_positions' must be a list or tuple",
    ):
        manager.reconcile(
            eagle_positions=[],
            broker_positions="invalid",  # type: ignore[arg-type]
        )


def test_invalid_position_entry_is_rejected() -> None:
    """Every position entry must be a dictionary."""

    manager = ReconciliationManager()

    with pytest.raises(
        TypeError,
        match="'eagle_positions' must contain only dictionaries",
    ):
        manager.reconcile(
            eagle_positions=[
                "invalid"
            ],  # type: ignore[list-item]
            broker_positions=[],
        )


def test_last_result_updates_after_reconciliation() -> None:
    """Manager should retain its most recent reconciliation result."""

    manager = ReconciliationManager()

    result = manager.reconcile(
        eagle_positions=[],
        broker_positions=[],
    )

    assert manager.last_result is result
    assert manager.last_result.status is ReconciliationStatus.MATCHED