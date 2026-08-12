"""Tests for BTS Interactive Brokers order construction."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ibapi.contract import Contract
from ibapi.order import Order

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.ib_order_factory import (
    IBOrderFactory,
    IBOrderPackage,
)


def create_trade_request(
    *,
    intent_value: str = "BUY_TO_OPEN",
    symbol: str = "MBT",
    quantity: int = 1,
) -> TradeRequest:
    """Create a deterministic TradeRequest."""

    return TradeRequest(
        event_id="ib-order-event-001",
        signal_id="ib-order-signal-001",
        timestamp=datetime(
            2026,
            8,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent(
            intent_value
        ),
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def create_factory(
    *,
    transmit: bool = False,
) -> IBOrderFactory:
    """Create the standard offline-test IB order factory."""

    return IBOrderFactory(
        exchange="CMECRYPTO",
        currency="USD",
        trading_class="MBT",
        order_type="MKT",
        time_in_force="DAY",
        transmit=transmit,
    )


def test_factory_retains_configuration() -> None:
    """Factory should expose its configured IB values."""

    factory = create_factory()

    assert factory.exchange == "CMECRYPTO"
    assert factory.currency == "USD"
    assert factory.trading_class == "MBT"
    assert factory.order_type == "MKT"
    assert factory.time_in_force == "DAY"
    assert factory.transmit is False


def test_create_returns_ib_order_package() -> None:
    """Factory should return a typed package."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert isinstance(
        result,
        IBOrderPackage,
    )


def test_package_contains_official_contract() -> None:
    """Package contract must use official ibapi Contract."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert isinstance(
        result.contract,
        Contract,
    )


def test_package_contains_official_order() -> None:
    """Package order must use official ibapi Order."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert isinstance(
        result.order,
        Order,
    )


def test_contract_uses_futures_security_type() -> None:
    """MBT contract should be represented as a future."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.contract.secType == "FUT"


def test_contract_symbol_is_preserved() -> None:
    """TradeRequest symbol should flow to the IB contract."""

    result = create_factory().create(
        create_trade_request(
            symbol="MBT"
        ),
        contract_month="202608",
    )

    assert result.contract.symbol == "MBT"


def test_contract_symbol_is_normalized() -> None:
    """IB contract symbols should be uppercase."""

    result = create_factory().create(
        create_trade_request(
            symbol="mbt"
        ),
        contract_month="202608",
    )

    assert result.contract.symbol == "MBT"


def test_contract_exchange_is_configured_value() -> None:
    """Factory should not invent exchange routing."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert (
        result.contract.exchange
        == "CMECRYPTO"
    )


def test_contract_currency_is_configured_value() -> None:
    """Contract should carry configured currency."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.contract.currency == "USD"


def test_contract_trading_class_is_configured() -> None:
    """Contract should carry configured trading class."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.contract.tradingClass == "MBT"


def test_contract_month_is_preserved() -> None:
    """Futures expiry identifier should flow to IB contract."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert (
        result.contract.lastTradeDateOrContractMonth
        == "202608"
    )


def test_eight_digit_contract_date_is_allowed() -> None:
    """Factory should support a full YYYYMMDD expiry value."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="20260828",
    )

    assert (
        result.contract.lastTradeDateOrContractMonth
        == "20260828"
    )


@pytest.mark.parametrize(
    (
        "intent_value",
        "expected_action",
    ),
    [
        (
            "BUY_TO_OPEN",
            "BUY",
        ),
        (
            "BUY_TO_CLOSE",
            "BUY",
        ),
        (
            "SELL_TO_OPEN",
            "SELL",
        ),
        (
            "SELL_TO_CLOSE",
            "SELL",
        ),
    ],
)
def test_trade_intent_maps_to_ib_action(
    intent_value: str,
    expected_action: str,
) -> None:
    """All BTS trade intents should map to BUY or SELL."""

    result = create_factory().create(
        create_trade_request(
            intent_value=intent_value
        ),
        contract_month="202608",
    )

    assert (
        result.order.action
        == expected_action
    )


def test_order_quantity_matches_trade_request() -> None:
    """Trade quantity should flow to official IB Order."""

    result = create_factory().create(
        create_trade_request(
            quantity=7
        ),
        contract_month="202608",
    )

    assert result.order.totalQuantity == 7


def test_order_type_is_configured() -> None:
    """IB order type should use factory configuration."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.order.orderType == "MKT"


def test_time_in_force_is_configured() -> None:
    """IB order time-in-force should use configuration."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.order.tif == "DAY"


def test_orders_default_to_transmit_false() -> None:
    """Development orders must not transmit by default."""

    factory = IBOrderFactory(
        exchange="CMECRYPTO"
    )

    result = factory.create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.order.transmit is False


def test_transmit_true_requires_explicit_factory_setting() -> None:
    """Transmit state should only change explicitly."""

    result = create_factory(
        transmit=True
    ).create(
        create_trade_request(),
        contract_month="202608",
    )

    assert result.order.transmit is True


def test_package_preserves_event_id() -> None:
    """Execution package should retain Eagle event identity."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert (
        result.event_id
        == "ib-order-event-001"
    )


def test_package_preserves_signal_id() -> None:
    """Execution package should retain Eagle signal identity."""

    result = create_factory().create(
        create_trade_request(),
        contract_month="202608",
    )

    assert (
        result.signal_id
        == "ib-order-signal-001"
    )


@pytest.mark.parametrize(
    "invalid_contract_month",
    [
        "",
        "   ",
        "2026",
        "2026081",
        "202608100",
        "ABCDEF",
        "2026-08",
    ],
)
def test_invalid_contract_month_is_rejected(
    invalid_contract_month: str,
) -> None:
    """Contract expiry must be 6 or 8 numeric characters."""

    with pytest.raises(
        ValueError,
        match="'contract_month'",
    ):
        create_factory().create(
            create_trade_request(),
            contract_month=invalid_contract_month,
        )


def test_invalid_trade_request_is_rejected() -> None:
    """Factory requires a TradeRequest."""

    with pytest.raises(
        TypeError,
        match="'trade_request' must be a TradeRequest",
    ):
        create_factory().create(
            object(),  # type: ignore[arg-type]
            contract_month="202608",
        )


@pytest.mark.parametrize(
    "invalid_exchange",
    [
        "",
        "   ",
    ],
)
def test_invalid_exchange_is_rejected(
    invalid_exchange: str,
) -> None:
    """Exchange must be explicitly configured."""

    with pytest.raises(
        ValueError,
        match="'exchange' must be a non-empty string",
    ):
        IBOrderFactory(
            exchange=invalid_exchange
        )


def test_invalid_transmit_type_is_rejected() -> None:
    """Transmit setting must be an explicit bool."""

    with pytest.raises(
        TypeError,
        match="'transmit' must be a bool",
    ):
        IBOrderFactory(
            exchange="CMECRYPTO",
            transmit=1,  # type: ignore[arg-type]
        )

def test_market_factory_has_no_limit_price() -> None:
    """Market-order factory should have no limit price."""

    factory = create_factory()

    assert factory.limit_price is None


def test_limit_factory_retains_limit_price() -> None:
    """Limit price should be normalized to Decimal."""

    factory = IBOrderFactory(
        exchange="CME",
        currency="USD",
        trading_class="MBT",
        order_type="LMT",
        time_in_force="DAY",
        limit_price=63250,
    )

    assert (
        factory.limit_price
        == Decimal("63250")
    )


def test_limit_order_type_is_lmt() -> None:
    """Official IB limit order should carry LMT order type."""

    factory = IBOrderFactory(
        exchange="CME",
        trading_class="MBT",
        order_type="LMT",
        limit_price=63250,
    )

    result = factory.create(
        create_trade_request(),
        contract_month="20260828",
    )

    assert result.order.orderType == "LMT"


def test_limit_order_carries_configured_price() -> None:
    """Official IB limit order must carry configured price."""

    factory = IBOrderFactory(
        exchange="CME",
        trading_class="MBT",
        order_type="LMT",
        limit_price=63250,
    )

    result = factory.create(
        create_trade_request(),
        contract_month="20260828",
    )

    assert result.order.lmtPrice == 63250.0


def test_limit_price_accepts_decimal_text() -> None:
    """Decimal-compatible limit values should normalize correctly."""

    factory = IBOrderFactory(
        exchange="CME",
        trading_class="MBT",
        order_type="LMT",
        limit_price="63250.50",
    )

    assert (
        factory.limit_price
        == Decimal("63250.50")
    )

    result = factory.create(
        create_trade_request(),
        contract_month="20260828",
    )

    assert result.order.lmtPrice == 63250.50


def test_limit_order_defaults_to_transmit_false() -> None:
    """Limit-order support must preserve safe default transmission."""

    factory = IBOrderFactory(
        exchange="CME",
        trading_class="MBT",
        order_type="LMT",
        limit_price=63250,
    )

    result = factory.create(
        create_trade_request(),
        contract_month="20260828",
    )

    assert result.order.transmit is False


def test_limit_order_can_explicitly_transmit() -> None:
    """Limit order may transmit only when explicitly enabled."""

    factory = IBOrderFactory(
        exchange="CME",
        trading_class="MBT",
        order_type="LMT",
        limit_price=63250,
        transmit=True,
    )

    result = factory.create(
        create_trade_request(),
        contract_month="20260828",
    )

    assert result.order.transmit is True


def test_resting_buy_limit_order_is_constructed_correctly() -> None:
    """Working-order test configuration should create BUY 1 at 63250."""

    factory = IBOrderFactory(
        exchange="CME",
        currency="USD",
        trading_class="MBT",
        order_type="LMT",
        time_in_force="DAY",
        transmit=True,
        limit_price=63250,
    )

    result = factory.create(
        create_trade_request(
            intent_value="BUY_TO_OPEN",
            quantity=1,
        ),
        contract_month="20260828",
    )

    assert result.order.action == "BUY"
    assert result.order.totalQuantity == 1
    assert result.order.orderType == "LMT"
    assert result.order.lmtPrice == 63250.0
    assert result.order.transmit is True


def test_lmt_without_limit_price_is_rejected() -> None:
    """A limit order must never exist without a price."""

    with pytest.raises(
        ValueError,
        match="'limit_price' is required",
    ):
        IBOrderFactory(
            exchange="CME",
            order_type="LMT",
        )


@pytest.mark.parametrize(
    "invalid_limit_price",
    [
        0,
        -1,
        True,
        "invalid",
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_invalid_limit_price_is_rejected(
    invalid_limit_price: object,
) -> None:
    """Limit prices must be finite positive values."""

    with pytest.raises(
        ValueError,
        match="'limit_price' must be a positive number",
    ):
        IBOrderFactory(
            exchange="CME",
            order_type="LMT",
            limit_price=invalid_limit_price,
        )


def test_market_order_rejects_limit_price() -> None:
    """Market orders must not silently ignore a limit price."""

    with pytest.raises(
        ValueError,
        match="'limit_price' may only be supplied",
    ):
        IBOrderFactory(
            exchange="CME",
            order_type="MKT",
            limit_price=63250,
        )