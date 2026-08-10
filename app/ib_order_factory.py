"""Build official IBKR Contract and Order objects from BTS trade requests."""

from dataclasses import dataclass

from ibapi.contract import Contract
from ibapi.order import Order

from app.communications.protocol import TradeIntent
from app.communications.trade_request import TradeRequest


@dataclass(frozen=True, slots=True)
class IBOrderPackage:
    """Immutable wrapper around one IBKR contract/order pair."""

    event_id: str
    signal_id: str
    contract: Contract
    order: Order


class IBOrderFactory:
    """Translate approved BTS TradeRequests into IB API objects.

    This factory does not connect to Interactive Brokers and does
    not call placeOrder().

    Orders created here default to transmit=False as an additional
    safety barrier during development.
    """

    def __init__(
        self,
        *,
        exchange: str,
        currency: str = "USD",
        trading_class: str = "MBT",
        order_type: str = "MKT",
        time_in_force: str = "DAY",
        transmit: bool = False,
    ) -> None:
        """Create an IB order factory."""

        self._exchange = self._validate_text(
            exchange,
            "exchange",
        )

        self._currency = self._validate_text(
            currency,
            "currency",
        ).upper()

        self._trading_class = self._validate_text(
            trading_class,
            "trading_class",
        ).upper()

        self._order_type = self._validate_text(
            order_type,
            "order_type",
        ).upper()

        self._time_in_force = self._validate_text(
            time_in_force,
            "time_in_force",
        ).upper()

        if not isinstance(
            transmit,
            bool,
        ):
            raise TypeError(
                "'transmit' must be a bool."
            )

        self._transmit = transmit

    @property
    def exchange(self) -> str:
        """Return the configured IBKR exchange value."""

        return self._exchange

    @property
    def currency(self) -> str:
        """Return the configured contract currency."""

        return self._currency

    @property
    def trading_class(self) -> str:
        """Return the configured IB trading class."""

        return self._trading_class

    @property
    def order_type(self) -> str:
        """Return the configured IB order type."""

        return self._order_type

    @property
    def time_in_force(self) -> str:
        """Return the configured IB time-in-force."""

        return self._time_in_force

    @property
    def transmit(self) -> bool:
        """Return whether generated IB orders may transmit."""

        return self._transmit

    def create(
        self,
        trade_request: TradeRequest,
        *,
        contract_month: str,
    ) -> IBOrderPackage:
        """Create an IBKR Contract and Order from a TradeRequest.

        Args:
            trade_request:
                A BTS TradeRequest that has already passed risk
                evaluation before reaching the execution layer.

            contract_month:
                IB futures expiry identifier, normally supplied in
                YYYYMM or YYYYMMDD form by the execution configuration.

        Returns:
            An IBOrderPackage containing official ibapi objects.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        normalized_contract_month = (
            self._validate_contract_month(
                contract_month
            )
        )

        contract = self._create_contract(
            trade_request,
            contract_month=normalized_contract_month,
        )

        order = self._create_order(
            trade_request
        )

        return IBOrderPackage(
            event_id=trade_request.event_id,
            signal_id=trade_request.signal_id,
            contract=contract,
            order=order,
        )

    def _create_contract(
        self,
        trade_request: TradeRequest,
        *,
        contract_month: str,
    ) -> Contract:
        """Build the official IB futures Contract."""

        symbol = self._validate_text(
            trade_request.symbol,
            "trade_request.symbol",
        ).upper()

        contract = Contract()

        contract.symbol = symbol
        contract.secType = "FUT"
        contract.exchange = self._exchange
        contract.currency = self._currency

        contract.lastTradeDateOrContractMonth = (
            contract_month
        )

        contract.tradingClass = (
            self._trading_class
        )

        return contract

    def _create_order(
        self,
        trade_request: TradeRequest,
    ) -> Order:
        """Build the official IB Order object."""

        quantity = trade_request.quantity

        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
        ):
            raise ValueError(
                "TradeRequest quantity must be "
                "a positive integer."
            )

        action = self._action_for_intent(
            trade_request.intent
        )

        order = Order()

        order.action = action
        order.totalQuantity = quantity
        order.orderType = self._order_type
        order.tif = self._time_in_force
        order.transmit = self._transmit

        return order

    @staticmethod
    def _action_for_intent(
        intent: TradeIntent,
    ) -> str:
        """Map BTS intent to the corresponding IB BUY/SELL action."""

        if not isinstance(
            intent,
            TradeIntent,
        ):
            raise TypeError(
                "'intent' must be a TradeIntent."
            )

        if intent in {
            TradeIntent.BUY_TO_OPEN,
            TradeIntent.BUY_TO_CLOSE,
        }:
            return "BUY"

        if intent in {
            TradeIntent.SELL_TO_OPEN,
            TradeIntent.SELL_TO_CLOSE,
        }:
            return "SELL"

        raise ValueError(
            f"Unsupported TradeIntent: {intent!r}."
        )

    @staticmethod
    def _validate_contract_month(
        contract_month: str,
    ) -> str:
        """Validate IB futures contract month/date text."""

        if (
            not isinstance(contract_month, str)
            or not contract_month.strip()
        ):
            raise ValueError(
                "'contract_month' must be a non-empty string."
            )

        normalized = (
            contract_month.strip()
        )

        if (
            not normalized.isdigit()
            or len(normalized) not in {
                6,
                8,
            }
        ):
            raise ValueError(
                "'contract_month' must contain "
                "6 or 8 numeric characters."
            )

        return normalized

    @staticmethod
    def _validate_text(
        value: str,
        field_name: str,
    ) -> str:
        """Validate a required non-empty string."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                f"'{field_name}' must be a non-empty string."
            )

        return value.strip()