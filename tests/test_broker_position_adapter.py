"""Tests for broker-position normalization."""

import pytest

from app.broker_position import BrokerPosition
from app.broker_position_adapter import (
    BrokerPositionAdapter,
    RawBrokerPosition,
)


def test_empty_raw_positions_return_empty_tuple() -> None:
    """An empty broker book should remain empty."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        []
    )

    assert result == ()


def test_positive_quantity_becomes_long() -> None:
    """Positive broker quantity should normalize to LONG."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        [
            RawBrokerPosition(
                symbol="MBT",
                quantity=2,
                signal_id="signal-001",
            )
        ]
    )

    assert result == (
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=2,
            signal_id="signal-001",
        ),
    )


def test_negative_quantity_becomes_short() -> None:
    """Negative broker quantity should normalize to SHORT."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        [
            RawBrokerPosition(
                symbol="MBT",
                quantity=-3,
                signal_id="signal-002",
            )
        ]
    )

    assert result == (
        BrokerPosition(
            symbol="MBT",
            side="SHORT",
            quantity=3,
            signal_id="signal-002",
        ),
    )


def test_zero_quantity_is_ignored() -> None:
    """Flat broker positions should not appear in reconciliation."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        [
            RawBrokerPosition(
                symbol="MBT",
                quantity=0,
            )
        ]
    )

    assert result == ()


def test_multiple_positions_are_normalized() -> None:
    """Multiple raw broker positions should be preserved in order."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        [
            RawBrokerPosition(
                symbol="MBT",
                quantity=1,
                signal_id="signal-001",
            ),
            RawBrokerPosition(
                symbol="MES",
                quantity=-2,
                signal_id="signal-002",
            ),
        ]
    )

    assert result == (
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=1,
            signal_id="signal-001",
        ),
        BrokerPosition(
            symbol="MES",
            side="SHORT",
            quantity=2,
            signal_id="signal-002",
        ),
    )


def test_symbol_is_normalized_by_broker_position() -> None:
    """Adapter should reuse BrokerPosition symbol normalization."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        [
            RawBrokerPosition(
                symbol=" mbt ",
                quantity=1,
            )
        ]
    )

    assert result[0].symbol == "MBT"


def test_signal_id_is_optional() -> None:
    """Raw broker positions need not contain an Eagle signal ID."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        [
            RawBrokerPosition(
                symbol="MBT",
                quantity=1,
            )
        ]
    )

    assert result[0].signal_id is None


def test_tuple_input_is_supported() -> None:
    """Raw positions may be supplied as a tuple."""

    adapter = BrokerPositionAdapter()

    result = adapter.adapt(
        (
            RawBrokerPosition(
                symbol="MBT",
                quantity=1,
            ),
        )
    )

    assert len(result) == 1
    assert result[0].side == "LONG"


def test_invalid_collection_type_is_rejected() -> None:
    """Raw broker positions must be supplied as list or tuple."""

    adapter = BrokerPositionAdapter()

    with pytest.raises(
        TypeError,
        match="'raw_positions' must be a list or tuple",
    ):
        adapter.adapt(
            "invalid"  # type: ignore[arg-type]
        )


def test_invalid_entry_type_is_rejected() -> None:
    """Each raw position must use RawBrokerPosition."""

    adapter = BrokerPositionAdapter()

    with pytest.raises(
        TypeError,
        match=(
            "'raw_positions' must contain only "
            "RawBrokerPosition objects"
        ),
    ):
        adapter.adapt(
            [
                {
                    "symbol": "MBT",
                    "quantity": 1,
                }
            ]  # type: ignore[list-item]
        )


def test_empty_symbol_is_rejected() -> None:
    """Raw broker symbols must contain text."""

    adapter = BrokerPositionAdapter()

    with pytest.raises(
        ValueError,
        match="symbol",
    ):
        adapter.adapt(
            [
                RawBrokerPosition(
                    symbol="",
                    quantity=1,
                )
            ]
        )


def test_non_integer_quantity_is_rejected() -> None:
    """Raw broker quantity must be an integer."""

    adapter = BrokerPositionAdapter()

    with pytest.raises(
        ValueError,
        match="quantity",
    ):
        adapter.adapt(
            [
                RawBrokerPosition(
                    symbol="MBT",
                    quantity=1.5,  # type: ignore[arg-type]
                )
            ]
        )


def test_boolean_quantity_is_rejected() -> None:
    """Boolean values must not be accepted as raw quantities."""

    adapter = BrokerPositionAdapter()

    with pytest.raises(
        ValueError,
        match="quantity",
    ):
        adapter.adapt(
            [
                RawBrokerPosition(
                    symbol="MBT",
                    quantity=True,  # type: ignore[arg-type]
                )
            ]
        )