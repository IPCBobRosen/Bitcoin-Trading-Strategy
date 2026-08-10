"""Tests for normalized broker positions."""

import pytest

from app.broker_position import BrokerPosition


def test_valid_position_is_created() -> None:
    """A valid broker position should be preserved."""

    position = BrokerPosition(
        symbol="MBT",
        side="LONG",
        quantity=2,
        signal_id="signal-001",
    )

    assert position.symbol == "MBT"
    assert position.side == "LONG"
    assert position.quantity == 2
    assert position.signal_id == "signal-001"


def test_symbol_is_normalized() -> None:
    """Broker symbols should be trimmed and uppercased."""

    position = BrokerPosition(
        symbol=" mbt ",
        side="LONG",
        quantity=1,
    )

    assert position.symbol == "MBT"


def test_side_is_normalized() -> None:
    """Position side should be trimmed and uppercased."""

    position = BrokerPosition(
        symbol="MBT",
        side=" long ",
        quantity=1,
    )

    assert position.side == "LONG"


def test_signal_id_is_optional() -> None:
    """Broker position may exist without an Eagle signal ID."""

    position = BrokerPosition(
        symbol="MBT",
        side="SHORT",
        quantity=1,
    )

    assert position.signal_id is None


def test_signal_id_is_trimmed() -> None:
    """Optional signal IDs should be normalized."""

    position = BrokerPosition(
        symbol="MBT",
        side="LONG",
        quantity=1,
        signal_id=" signal-001 ",
    )

    assert position.signal_id == "signal-001"


def test_to_dict_includes_signal_id_when_present() -> None:
    """Dictionary conversion should preserve correlation information."""

    position = BrokerPosition(
        symbol="MBT",
        side="LONG",
        quantity=2,
        signal_id="signal-001",
    )

    assert position.to_dict() == {
        "symbol": "MBT",
        "side": "LONG",
        "quantity": 2,
        "signal_id": "signal-001",
    }


def test_to_dict_omits_missing_signal_id() -> None:
    """Absent signal IDs should not create a null comparison field."""

    position = BrokerPosition(
        symbol="MBT",
        side="SHORT",
        quantity=1,
    )

    assert position.to_dict() == {
        "symbol": "MBT",
        "side": "SHORT",
        "quantity": 1,
    }


def test_empty_symbol_is_rejected() -> None:
    """Broker symbol must contain text."""

    with pytest.raises(
        ValueError,
        match="'symbol' must be a non-empty string",
    ):
        BrokerPosition(
            symbol="",
            side="LONG",
            quantity=1,
        )


def test_invalid_side_is_rejected() -> None:
    """Only LONG and SHORT are valid normalized sides."""

    with pytest.raises(
        ValueError,
        match="'side' must be either 'LONG' or 'SHORT'",
    ):
        BrokerPosition(
            symbol="MBT",
            side="FLAT",
            quantity=1,
        )


def test_zero_quantity_is_rejected() -> None:
    """Open positions must have positive quantity."""

    with pytest.raises(
        ValueError,
        match="'quantity' must be a positive integer",
    ):
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=0,
        )


def test_negative_quantity_is_rejected() -> None:
    """Negative broker quantity is not a normalized position."""

    with pytest.raises(
        ValueError,
        match="'quantity' must be a positive integer",
    ):
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=-1,
        )


def test_boolean_quantity_is_rejected() -> None:
    """Boolean values must not be accepted as quantities."""

    with pytest.raises(
        ValueError,
        match="'quantity' must be a positive integer",
    ):
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=True,  # type: ignore[arg-type]
        )


def test_empty_signal_id_is_rejected() -> None:
    """A supplied signal ID must contain text."""

    with pytest.raises(
        ValueError,
        match="'signal_id' must be a non-empty string",
    ):
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=1,
            signal_id="",
        )


def test_position_is_immutable() -> None:
    """Normalized broker snapshots must not change after creation."""

    position = BrokerPosition(
        symbol="MBT",
        side="LONG",
        quantity=1,
    )

    with pytest.raises(
        AttributeError,
    ):
        position.quantity = 2  # type: ignore[misc]