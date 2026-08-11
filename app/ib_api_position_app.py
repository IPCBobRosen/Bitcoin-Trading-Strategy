"""Interactive Brokers API application bridge for BTS."""

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.execution import Execution
from ibapi.order import Order
from ibapi.order_state import OrderState
from ibapi.wrapper import EWrapper

from app.execution_ledger import ExecutionLedger
from app.ib_api_ready import IBApiReady
from app.ib_broker_client import IBBrokerClient
from app.ib_error_handler import (
    IBErrorHandler,
    IBErrorResult,
)
from app.ib_execution_details_transport import (
    IBExecutionDetailsTransport,
)
from app.ib_order_id_allocator import IBOrderIdAllocator
from app.ib_order_status_transport import (
    IBOrderStatusTransport,
)
from app.ib_position_transport import IBPositionTransport
from app.kill_switch import KillSwitch


class IBApiPositionApp(EWrapper, EClient):
    """IBKR EWrapper/EClient bridge for BTS broker callbacks.

    The application always supports:

    - API handshake readiness;
    - IB order-ID allocation;
    - position snapshots;
    - emergency kill-switch state.

    When an ExecutionLedger is supplied, it additionally supports:

    - orderStatus callbacks;
    - execDetails callbacks;
    - IB error classification;
    - durable execution-state updates.

    Broker orders that are not represented in the BTS execution
    ledger do not manufacture BTS execution records. Their order
    IDs may still advance the safe future IB order-ID floor.
    """

    def __init__(
        self,
        broker_client: IBBrokerClient,
        *,
        execution_ledger: ExecutionLedger | None = None,
        order_id_allocator: IBOrderIdAllocator | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        """Create the BTS Interactive Brokers API application."""

        if not isinstance(
            broker_client,
            IBBrokerClient,
        ):
            raise TypeError(
                "'broker_client' must be an IBBrokerClient."
            )

        if (
            execution_ledger is not None
            and not isinstance(
                execution_ledger,
                ExecutionLedger,
            )
        ):
            raise TypeError(
                "'execution_ledger' must be an "
                "ExecutionLedger or None."
            )

        if (
            order_id_allocator is not None
            and not isinstance(
                order_id_allocator,
                IBOrderIdAllocator,
            )
        ):
            raise TypeError(
                "'order_id_allocator' must be an "
                "IBOrderIdAllocator or None."
            )

        if (
            kill_switch is not None
            and not isinstance(
                kill_switch,
                KillSwitch,
            )
        ):
            raise TypeError(
                "'kill_switch' must be a "
                "KillSwitch or None."
            )

        EWrapper.__init__(
            self
        )

        EClient.__init__(
            self,
            wrapper=self,
        )

        self._broker_client = broker_client

        self._position_transport = (
            IBPositionTransport(
                broker_client
            )
        )

        self._api_ready = IBApiReady()

        self._order_id_allocator = (
            order_id_allocator
            if order_id_allocator is not None
            else IBOrderIdAllocator()
        )

        self._kill_switch = (
            kill_switch
            if kill_switch is not None
            else KillSwitch()
        )

        self._execution_ledger = (
            execution_ledger
        )

        if execution_ledger is None:
            self._order_status_transport = None
            self._execution_details_transport = None
            self._error_handler = None

        else:
            self._order_status_transport = (
                IBOrderStatusTransport(
                    execution_ledger
                )
            )

            self._execution_details_transport = (
                IBExecutionDetailsTransport(
                    execution_ledger
                )
            )

            self._error_handler = (
                IBErrorHandler(
                    execution_ledger=execution_ledger,
                    kill_switch=self._kill_switch,
                )
            )

        self._position_request_active = False

        self._last_error_result: (
            IBErrorResult | None
        ) = None

    @property
    def broker_client(self) -> IBBrokerClient:
        """Return the BTS IB broker client."""

        return self._broker_client

    @property
    def position_transport(
        self,
    ) -> IBPositionTransport:
        """Return the IB position callback transport."""

        return self._position_transport

    @property
    def api_ready(self) -> IBApiReady:
        """Return the IB API handshake-readiness tracker."""

        return self._api_ready

    @property
    def order_id_allocator(
        self,
    ) -> IBOrderIdAllocator:
        """Return the IB order-ID allocator."""

        return self._order_id_allocator

    @property
    def kill_switch(self) -> KillSwitch:
        """Return the BTS emergency kill switch."""

        return self._kill_switch

    @property
    def execution_ledger(
        self,
    ) -> ExecutionLedger | None:
        """Return the durable execution ledger when configured."""

        return self._execution_ledger

    @property
    def order_status_transport(
        self,
    ) -> IBOrderStatusTransport | None:
        """Return the IB order-status transport when configured."""

        return self._order_status_transport

    @property
    def execution_details_transport(
        self,
    ) -> IBExecutionDetailsTransport | None:
        """Return the IB execution-details transport."""

        return self._execution_details_transport

    @property
    def error_handler(
        self,
    ) -> IBErrorHandler | None:
        """Return the IB error handler when configured."""

        return self._error_handler

    @property
    def last_error_result(
        self,
    ) -> IBErrorResult | None:
        """Return the most recently classified IB message."""

        return self._last_error_result

    @property
    def position_request_active(self) -> bool:
        """Return True while a position subscription is active."""

        return self._position_request_active

    def request_position_snapshot(self) -> None:
        """Request the current IBKR position snapshot."""

        if self._position_request_active:
            raise RuntimeError(
                "IB position request is already active."
            )

        self._position_transport.begin_snapshot()

        self._position_request_active = True

        self.reqPositions()

    def cancel_position_updates(self) -> None:
        """Cancel the IBKR position subscription."""

        if not self._position_request_active:
            return

        self.cancelPositions()

        self._position_request_active = False

    def nextValidId(
        self,
        orderId: int,
    ) -> None:
        """Receive IBKR API readiness and next order ID."""

        self._api_ready.record_next_valid_id(
            orderId
        )

        self._order_id_allocator.initialize(
            orderId
        )

    def openOrder(
        self,
        orderId: int,
        contract: Contract,
        order: Order,
        orderState: OrderState,
    ) -> None:
        """Receive one IBKR open-order callback."""

        self._order_id_allocator.observe_order_id(
            orderId
        )

    def orderStatus(
        self,
        orderId: int,
        status: str,
        filled,
        remaining,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        """Receive one IBKR orderStatus callback."""

        self._order_id_allocator.observe_order_id(
            orderId
        )

        transport = (
            self._order_status_transport
        )

        if transport is None:
            return

        try:
            transport.handle_order_status(
                broker_order_id=orderId,
                status=status,
                filled=filled,
                remaining=remaining,
            )

        except KeyError:
            # IB may report an order that BTS did not create.
            # Its ID still matters for future safe allocation.
            return

    def execDetails(
        self,
        reqId: int,
        contract: Contract,
        execution: Execution,
    ) -> None:
        """Receive one IBKR execution-details callback."""

        broker_order_id = (
            execution.orderId
        )

        self._order_id_allocator.observe_order_id(
            broker_order_id
        )

        transport = (
            self._execution_details_transport
        )

        if transport is None:
            return

        try:
            transport.handle_execution(
                contract=contract,
                execution=execution,
            )

        except KeyError:
            # Execution belongs to an IB order outside BTS.
            return

    def error(
        self,
        reqId: int,
        errorTime: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson="",
    ) -> None:
        """Receive and classify one official IBKR error callback.

        TWS API 10.33+ supplies errorTime as a Unix timestamp.

        IB uses this callback for genuine errors, warnings, and
        informational notifications. Classification and BTS safety
        action are therefore delegated to IBErrorHandler.

        advancedOrderRejectJson is preserved in the audit message
        when IB supplies it.
        """

        handler = self._error_handler

        if handler is None:
            return

        message = errorString

        if (
            isinstance(
                advancedOrderRejectJson,
                str,
            )
            and advancedOrderRejectJson.strip()
        ):
            message = (
                f"{errorString} "
                "AdvancedOrderRejectJson: "
                f"{advancedOrderRejectJson.strip()}"
            )

        self._last_error_result = (
            handler.handle(
                request_id=reqId,
                error_code=errorCode,
                message=message,
            )
        )

    def position(
        self,
        account: str,
        contract: Contract,
        position,
        avgCost: float,
    ) -> None:
        """Receive one IBKR position callback."""

        self._position_transport.position(
            account=account,
            contract=contract,
            position=position,
            average_cost=avgCost,
        )

    def positionEnd(self) -> None:
        """Receive the IBKR initial-position snapshot completion."""

        self._position_transport.position_end()

    def connectionClosed(self) -> None:
        """Reset connection-specific local state.

        API readiness is invalidated immediately.

        Order-ID history is intentionally retained so a reconnect
        cannot accidentally make an old IB order ID reusable.
        """

        self._position_request_active = False

        self._api_ready.reset()