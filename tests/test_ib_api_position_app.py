"""Tests for the Interactive Brokers API position application."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

from app.broker_position_adapter import RawBrokerPosition
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient


def create_contract(
    *,
    symbol: str = "MBT",
    local_symbol: str = "MBTQ6",
    con_id: int = 123456,
) -> Contract:
    """Create a minimal official IBKR Contract for position tests."""

    contract = Contract()

    contract.symbol = symbol
    contract.localSymbol = local_symbol
    contract.conId = con_id
    contract.tradingClass = "MBT"
    contract.lastTradeDateOrContractMonth = "20260828"

    return contract


def test_app_is_ib_wrapper_and_client() -> None:
    """IB API app should use the official EWrapper/EClient classes."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    assert isinstance(
        app,
        EWrapper,
    )

    assert isinstance(
        app,
        EClient,
    )


def test_invalid_broker_client_is_rejected() -> None:
    """IB API app requires an IBBrokerClient."""

    with pytest.raises(
        TypeError,
        match="'broker_client' must be an IBBrokerClient",
    ):
        IBApiPositionApp(
            object()  # type: ignore[arg-type]
        )


def test_app_retains_broker_client() -> None:
    """App should retain the BTS broker client."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    assert app.broker_client is broker_client


def test_new_app_is_not_api_ready() -> None:
    """New IB app should not be handshake-ready."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    assert app.api_ready.ready is False
    assert app.api_ready.next_valid_order_id is None


def test_next_valid_id_marks_api_ready() -> None:
    """IB nextValidId should mark handshake readiness."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert app.api_ready.ready is True
    assert app.api_ready.next_valid_order_id == 100


def test_invalid_next_valid_id_is_rejected() -> None:
    """Invalid IB order IDs must not mark readiness."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    with pytest.raises(
        ValueError,
        match="'order_id' must be a non-negative integer",
    ):
        app.nextValidId(
            -1
        )

    assert app.api_ready.ready is False


def test_position_request_starts_snapshot() -> None:
    """Requesting positions should begin BTS snapshot collection."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ) as req_positions:
        app.request_position_snapshot()

    assert broker_client.snapshot_in_progress is True
    assert broker_client.snapshot_complete is False
    assert app.position_request_active is True

    req_positions.assert_called_once_with()


def test_duplicate_position_request_is_rejected() -> None:
    """Only one position subscription should be active at a time."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

        with pytest.raises(
            RuntimeError,
            match="already active",
        ):
            app.request_position_snapshot()


def test_position_callback_flows_into_broker_client() -> None:
    """Official IB position callback should reach BTS normalization."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(),
        position=Decimal("2"),
        avgCost=65000.0,
    )

    app.positionEnd()

    assert broker_client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=2,
            signal_id=None,
        ),
    )


def test_short_position_callback_is_preserved() -> None:
    """Negative IB position should remain signed for the adapter."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(),
        position=Decimal("-3"),
        avgCost=64000.0,
    )

    app.positionEnd()

    assert broker_client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBTQ6",
            quantity=-3,
            signal_id=None,
        ),
    )


def test_position_end_completes_snapshot() -> None:
    """positionEnd should mark the initial snapshot complete."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.positionEnd()

    assert broker_client.snapshot_in_progress is False
    assert broker_client.snapshot_complete is True


def test_cancel_position_updates_calls_ib_api() -> None:
    """Active IB position subscription should be cancelable."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    with patch.object(
        app,
        "cancelPositions",
    ) as cancel_positions:
        app.cancel_position_updates()

    cancel_positions.assert_called_once_with()

    assert app.position_request_active is False


def test_cancel_when_not_active_is_noop() -> None:
    """Cancel should do nothing when no subscription is active."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "cancelPositions",
    ) as cancel_positions:
        app.cancel_position_updates()

    cancel_positions.assert_not_called()


def test_connection_closed_resets_request_state() -> None:
    """IB connection loss should clear the active request flag."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    assert app.position_request_active is True

    app.connectionClosed()

    assert app.position_request_active is False


def test_connection_closed_resets_api_readiness() -> None:
    """IB connection loss should invalidate handshake readiness."""

    app = IBApiPositionApp(
        IBBrokerClient()
    )

    app.nextValidId(
        100
    )

    assert app.api_ready.ready is True

    app.connectionClosed()

    assert app.api_ready.ready is False
    assert app.api_ready.next_valid_order_id is None


def test_local_symbol_from_official_contract_is_used() -> None:
    """Official IB Contract.localSymbol should flow into BTS."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(
            symbol="MBT",
            local_symbol="MBTZ6",
        ),
        position=1,
        avgCost=60000.0,
    )

    app.positionEnd()

    assert (
        broker_client.get_raw_positions()[0].symbol
        == "MBTZ6"
    )


def test_empty_local_symbol_falls_back_to_root_symbol() -> None:
    """Root symbol should be used if IB localSymbol is unavailable."""

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client
    )

    with patch.object(
        app,
        "reqPositions",
    ):
        app.request_position_snapshot()

    app.position(
        account="DU123456",
        contract=create_contract(
            symbol="MBT",
            local_symbol="",
        ),
        position=1,
        avgCost=60000.0,
    )

    app.positionEnd()

    assert (
        broker_client.get_raw_positions()[0].symbol
        == "MBT"
    )