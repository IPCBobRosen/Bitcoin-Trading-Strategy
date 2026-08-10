"""Tests for broker-position providers."""

import pytest

from app.broker_position import BrokerPosition
from app.broker_position_adapter import (
    BrokerPositionAdapter,
    RawBrokerPosition,
)
from app.broker_position_provider import (
    AdapterBrokerPositionProvider,
    BrokerPositionProvider,
    StaticBrokerPositionProvider,
)


def test_static_provider_defaults_to_empty_positions() -> None:
    """A local provider should default to an empty broker book."""

    provider = StaticBrokerPositionProvider()

    assert provider.get_positions() == ()


def test_static_provider_returns_positions() -> None:
    """Configured broker positions should be returned unchanged."""

    position = BrokerPosition(
        symbol="MBT",
        side="LONG",
        quantity=2,
        signal_id="signal-001",
    )

    provider = StaticBrokerPositionProvider(
        [
            position
        ]
    )

    assert provider.get_positions() == (
        position,
    )


def test_static_provider_accepts_tuple() -> None:
    """Broker positions may be supplied as a tuple."""

    position = BrokerPosition(
        symbol="MBT",
        side="SHORT",
        quantity=1,
    )

    provider = StaticBrokerPositionProvider(
        (
            position,
        )
    )

    assert provider.get_positions() == (
        position,
    )


def test_static_provider_preserves_order() -> None:
    """Provider should preserve the supplied broker snapshot order."""

    first = BrokerPosition(
        symbol="MBT",
        side="LONG",
        quantity=1,
        signal_id="signal-001",
    )

    second = BrokerPosition(
        symbol="MBT",
        side="SHORT",
        quantity=2,
        signal_id="signal-002",
    )

    provider = StaticBrokerPositionProvider(
        [
            first,
            second,
        ]
    )

    assert provider.get_positions() == (
        first,
        second,
    )


def test_invalid_static_position_entry_is_rejected() -> None:
    """Static provider requires normalized BrokerPosition objects."""

    with pytest.raises(
        TypeError,
        match="'positions' must contain only BrokerPosition objects",
    ):
        StaticBrokerPositionProvider(
            [
                {
                    "symbol": "MBT",
                    "side": "LONG",
                    "quantity": 1,
                }
            ]  # type: ignore[list-item]
        )


def test_static_returned_positions_are_tuple() -> None:
    """Static provider should expose an immutable tuple snapshot."""

    provider = StaticBrokerPositionProvider()

    positions = provider.get_positions()

    assert isinstance(
        positions,
        tuple,
    )


def test_static_provider_satisfies_protocol_shape() -> None:
    """Static provider should expose the provider interface method."""

    provider: BrokerPositionProvider = (
        StaticBrokerPositionProvider()
    )

    assert provider.get_positions() == ()


def test_adapter_provider_returns_normalized_positions() -> None:
    """Raw broker positions should be normalized by the provider."""

    def raw_source() -> list[RawBrokerPosition]:
        return [
            RawBrokerPosition(
                symbol="mbt",
                quantity=2,
                signal_id="signal-001",
            )
        ]

    provider = AdapterBrokerPositionProvider(
        raw_source
    )

    assert provider.get_positions() == (
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=2,
            signal_id="signal-001",
        ),
    )


def test_adapter_provider_converts_negative_quantity_to_short() -> None:
    """Adapter-backed provider should normalize short positions."""

    def raw_source() -> list[RawBrokerPosition]:
        return [
            RawBrokerPosition(
                symbol="MBT",
                quantity=-3,
            )
        ]

    provider = AdapterBrokerPositionProvider(
        raw_source
    )

    result = provider.get_positions()

    assert result == (
        BrokerPosition(
            symbol="MBT",
            side="SHORT",
            quantity=3,
        ),
    )


def test_adapter_provider_ignores_flat_positions() -> None:
    """Zero raw broker quantity should be omitted."""

    def raw_source() -> list[RawBrokerPosition]:
        return [
            RawBrokerPosition(
                symbol="MBT",
                quantity=0,
            )
        ]

    provider = AdapterBrokerPositionProvider(
        raw_source
    )

    assert provider.get_positions() == ()


def test_adapter_provider_calls_source_each_time() -> None:
    """Provider should retrieve a fresh broker snapshot per request."""

    call_count = 0

    def raw_source() -> list[RawBrokerPosition]:
        nonlocal call_count

        call_count += 1

        return [
            RawBrokerPosition(
                symbol="MBT",
                quantity=call_count,
            )
        ]

    provider = AdapterBrokerPositionProvider(
        raw_source
    )

    first_result = provider.get_positions()
    second_result = provider.get_positions()

    assert first_result[0].quantity == 1
    assert second_result[0].quantity == 2
    assert call_count == 2


def test_adapter_provider_accepts_custom_adapter() -> None:
    """A BrokerPositionAdapter may be supplied explicitly."""

    adapter = BrokerPositionAdapter()

    def raw_source() -> tuple[RawBrokerPosition, ...]:
        return (
            RawBrokerPosition(
                symbol="MBT",
                quantity=1,
            ),
        )

    provider = AdapterBrokerPositionProvider(
        raw_source,
        adapter=adapter,
    )

    result = provider.get_positions()

    assert result == (
        BrokerPosition(
            symbol="MBT",
            side="LONG",
            quantity=1,
        ),
    )


def test_adapter_provider_rejects_non_callable_source() -> None:
    """Raw position source must be callable."""

    with pytest.raises(
        TypeError,
        match="'raw_position_source' must be callable",
    ):
        AdapterBrokerPositionProvider(
            []  # type: ignore[arg-type]
        )


def test_adapter_provider_rejects_invalid_adapter() -> None:
    """Custom adapter must be BrokerPositionAdapter."""

    def raw_source() -> list[RawBrokerPosition]:
        return []

    with pytest.raises(
        TypeError,
        match="'adapter' must be a BrokerPositionAdapter",
    ):
        AdapterBrokerPositionProvider(
            raw_source,
            adapter=object(),  # type: ignore[arg-type]
        )


def test_adapter_provider_satisfies_protocol_shape() -> None:
    """Adapter-backed provider should expose the provider interface."""

    def raw_source() -> list[RawBrokerPosition]:
        return []

    provider: BrokerPositionProvider = (
        AdapterBrokerPositionProvider(
            raw_source
        )
    )

    assert provider.get_positions() == ()