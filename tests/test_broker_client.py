"""Tests for broker-client position retrieval."""

import pytest

from app.broker_client import (
    BrokerClient,
    FakeBrokerClient,
)
from app.broker_position_adapter import RawBrokerPosition


def test_fake_client_defaults_to_empty_positions() -> None:
    """A fake broker client should default to an empty position book."""

    client = FakeBrokerClient()

    assert client.get_raw_positions() == ()


def test_fake_client_returns_configured_positions() -> None:
    """Configured raw positions should be returned unchanged."""

    position = RawBrokerPosition(
        symbol="MBT",
        quantity=2,
        signal_id="signal-001",
    )

    client = FakeBrokerClient(
        positions=(
            position,
        )
    )

    assert client.get_raw_positions() == (
        position,
    )


def test_fake_client_preserves_multiple_positions() -> None:
    """Multiple raw positions should be preserved in order."""

    first = RawBrokerPosition(
        symbol="MBT",
        quantity=1,
        signal_id="signal-001",
    )

    second = RawBrokerPosition(
        symbol="MES",
        quantity=-2,
        signal_id="signal-002",
    )

    client = FakeBrokerClient(
        positions=(
            first,
            second,
        )
    )

    assert client.get_raw_positions() == (
        first,
        second,
    )


def test_fake_client_rejects_invalid_position_entry() -> None:
    """Fake broker client requires RawBrokerPosition objects."""

    with pytest.raises(
        TypeError,
        match="'positions' must contain only RawBrokerPosition objects",
    ):
        FakeBrokerClient(
            positions=(
                {
                    "symbol": "MBT",
                    "quantity": 1,
                },
            )  # type: ignore[arg-type]
        )


def test_fake_client_satisfies_protocol_shape() -> None:
    """FakeBrokerClient should satisfy the BrokerClient interface."""

    client: BrokerClient = FakeBrokerClient()

    assert client.get_raw_positions() == ()


def test_returned_positions_are_tuple() -> None:
    """Broker-client snapshots should be immutable tuples."""

    client = FakeBrokerClient()

    result = client.get_raw_positions()

    assert isinstance(
        result,
        tuple,
    )