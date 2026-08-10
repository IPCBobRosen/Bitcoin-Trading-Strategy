"""Tests for the Interactive Brokers position callback transport."""

from decimal import Decimal

import pytest

from app.broker_position_adapter import RawBrokerPosition
from app.ib_broker_client import IBBrokerClient
from app.ib_position_transport import IBPositionTransport


class FakeIBContract:
    """Minimal Contract-like object for deterministic transport tests."""

    def __init__(
        self,
        *,
        symbol: str = "MBT",
        con_id: int = 123456,
        local_symbol: str = "MBTQ6",
        trading_class: str = "MBT",
        last_trade_date: str = "20260828",
    ) -> None:
        self.symbol = symbol
        self.conId = con_id
        self.localSymbol = local_symbol
        self.tradingClass = trading_class
        self.lastTradeDateOrContractMonth = last_trade_date


def test_transport_accepts_ib_broker_client() -> None:
    """Transport should retain its IBBrokerClient dependency."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    assert transport.broker_client is client


def test_invalid_broker_client_is_rejected() -> None:
    """Transport requires an IBBrokerClient."""

    with pytest.raises(
        TypeError,
        match="'broker_client' must be an IBBrokerClient",
    ):
        IBPositionTransport(
            object()  # type: ignore[arg-type]
        )


def test_begin_snapshot_starts_client_collection() -> None:
    """Starting transport collection should start the IB client snapshot."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    assert client.snapshot_in_progress is True
    assert client.snapshot_complete is False


def test_position_callback_creates_raw_long_position() -> None:
    """IB position callback should flow into the broker client."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    transport.position(
        account="DU123456",
        contract=FakeIBContract(),
        position=Decimal("2"),
        average_cost=65000.0,
    )

    transport.position_end()

    assert client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=2,
            signal_id=None,
        ),
    )


def test_position_callback_preserves_short_quantity() -> None:
    """Negative IB quantities should remain negative for normalization."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    transport.position(
        account="DU123456",
        contract=FakeIBContract(),
        position=Decimal("-3"),
        average_cost=64000.0,
    )

    transport.position_end()

    assert client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=-3,
            signal_id=None,
        ),
    )


def test_position_end_completes_snapshot() -> None:
    """IB positionEnd should make the snapshot available."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()
    transport.position_end()

    assert client.snapshot_in_progress is False
    assert client.snapshot_complete is True
    assert client.get_raw_positions() == ()


def test_local_symbol_is_passed_to_broker_client() -> None:
    """Transport should preserve IB localSymbol for futures identity."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    transport.position(
        account="DU123456",
        contract=FakeIBContract(
            symbol="MBT",
            local_symbol="MBTZ6",
        ),
        position=1,
        average_cost=60000.0,
    )

    transport.position_end()

    assert (
        client.get_raw_positions()[0].symbol
        == "MBTZ6"
    )


def test_root_symbol_used_when_local_symbol_is_empty() -> None:
    """IB broker client should fall back to root symbol."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    transport.position(
        account="DU123456",
        contract=FakeIBContract(
            symbol="MBT",
            local_symbol="",
        ),
        position=1,
        average_cost=60000.0,
    )

    transport.position_end()

    assert (
        client.get_raw_positions()[0].symbol
        == "MBT"
    )


def test_flat_position_is_ignored() -> None:
    """Zero IB positions should not enter the BTS position book."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    transport.position(
        account="DU123456",
        contract=FakeIBContract(),
        position=0,
        average_cost=0.0,
    )

    transport.position_end()

    assert client.get_raw_positions() == ()


def test_empty_account_is_rejected() -> None:
    """IB position callbacks require an account identifier."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    with pytest.raises(
        ValueError,
        match="'account' must be a non-empty string",
    ):
        transport.position(
            account="",
            contract=FakeIBContract(),
            position=1,
            average_cost=60000.0,
        )


def test_missing_contract_is_rejected() -> None:
    """IB position callback must contain a Contract object."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    with pytest.raises(
        TypeError,
        match="'contract' must be supplied",
    ):
        transport.position(
            account="DU123456",
            contract=None,
            position=1,
            average_cost=60000.0,
        )


def test_invalid_average_cost_is_rejected() -> None:
    """IB average cost must be numeric."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    with pytest.raises(
        ValueError,
        match="'average_cost' must be numeric",
    ):
        transport.position(
            account="DU123456",
            contract=FakeIBContract(),
            position=1,
            average_cost="invalid",  # type: ignore[arg-type]
        )


def test_invalid_contract_con_id_is_rejected() -> None:
    """IB conId must be an integer when supplied."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    contract = FakeIBContract()

    contract.conId = "invalid"

    with pytest.raises(
        ValueError,
        match="conId",
    ):
        transport.position(
            account="DU123456",
            contract=contract,
            position=1,
            average_cost=60000.0,
        )


def test_multiple_callbacks_form_one_snapshot() -> None:
    """Multiple IB callbacks should accumulate until positionEnd."""

    client = IBBrokerClient()

    transport = IBPositionTransport(
        client
    )

    transport.begin_snapshot()

    transport.position(
        account="DU123456",
        contract=FakeIBContract(
            symbol="MBT",
            local_symbol="MBTQ6",
        ),
        position=1,
        average_cost=60000.0,
    )

    transport.position(
        account="DU123456",
        contract=FakeIBContract(
            symbol="MES",
            local_symbol="MESU6",
        ),
        position=-2,
        average_cost=5000.0,
    )

    transport.position_end()

    assert client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=1,
            signal_id=None,
        ),
        RawBrokerPosition(
            symbol="MESU6",
            quantity=-2,
            signal_id=None,
        ),
    )