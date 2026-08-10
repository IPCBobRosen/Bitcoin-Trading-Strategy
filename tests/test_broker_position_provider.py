"""Tests for broker-position providers."""

import pytest

from app.broker_position import BrokerPosition
from app.broker_position_provider import (
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


def test_invalid_position_entry_is_rejected() -> None:
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


def test_returned_positions_are_immutable_tuple() -> None:
    """Provider should expose a tuple snapshot."""

    provider = StaticBrokerPositionProvider()

    positions = provider.get_positions()

    assert isinstance(
        positions,
        tuple,
    )


def test_static_provider_satisfies_protocol_shape() -> None:
    """The static provider should expose the provider interface method."""

    provider: BrokerPositionProvider = (
        StaticBrokerPositionProvider()
    )

    assert provider.get_positions() == ()