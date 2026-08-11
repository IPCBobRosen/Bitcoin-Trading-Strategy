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
from app.ib_execution_details_transport import (
    IBExecutionDetailsTransport,
)
from app.ib_order_id_allocator import IBOrderIdAllocator
from app.ib_order_status_transport import (
    IBOrderStatusTransport,
)
from app.ib_position_transport import IBPositionTransport


class IBApiPositionApp(EWrapper, EClient):
    """IBKR EWrapper/EClient bridge for BTS broker callbacks.

    The application always supports:

    - API handshake readiness;
    - IB order-ID allocation;
    - position snapshots.

    When an ExecutionLedger is supplied, it additionally supports:

    - orderStatus callbacks;
    - execDetails callbacks;
    - durable BTS execution-state updates.

    IB callbacks for broker orders that are not represented in the
    BTS execution ledger are ignored by the execution transports,
    while their order IDs are still observed by the allocator.

    This allows manually created or otherwise external IB orders to
    advance the safe order-ID floor without manufacturing BTS
    execution records.
    """

    def __init__(
        self,
        broker_client: IBBrokerClient,
        *,
        execution_ledger: ExecutionLedger | None = None,
        order_id_allocator: IBOrderIdAllocator | None = None,
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

        self._execution_ledger = (
            execution_ledger
        )

        if execution_ledger is None:
            self._order_status_transport = None
            self._execution_details_transport = None

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

        self._position_request_active = False

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
        """Return the IB execution-details transport when configured."""

        return self._execution_details_transport

    @property
    def position_request_active(self) -> bool:
        """Return True while a position subscription is active."""

        return self._position_request_active

    def request_position_snapshot(self) -> None:
        """Request the current IBKR position snapshot.

        This starts BTS snapshot collection before calling the
        official IBKR reqPositions() subscription method.
        """

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
        """Receive IBKR API readiness and next order ID.

        The existing IBApiReady tracker remains the connection
        readiness authority.

        The same callback also initializes or advances the
        thread-safe BTS IB order-ID allocator.
        """

        # Preserve the existing readiness validation behavior first.
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
        """Receive one IBKR open-order callback.

        The callback is used here to advance the safe future
        order-ID floor.

        Execution-state transitions remain the responsibility of
        orderStatus and execDetails.
        """

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
        """Receive one IBKR orderStatus callback.

        Every observed order ID advances the allocator floor.

        If execution tracking is configured, known BTS orders are
        passed into IBOrderStatusTransport.

        Orders that do not belong to BTS are deliberately ignored
        by the execution ledger while their IDs remain observed.
        """

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
            # IB can report orders that BTS did not create.
            # Their IDs still matter to future safe allocation,
            # but they must not manufacture BTS ledger records.
            return

    def execDetails(
        self,
        reqId: int,
        contract: Contract,
        execution: Execution,
    ) -> None:
        """Receive one IBKR execution-details callback.

        Actual executions for known BTS orders flow into the
        durable execution-details transport.

        Executions for orders not represented in the BTS ledger
        are ignored by BTS, while their broker order IDs still
        advance the allocator floor.
        """

        broker_order_id = execution.orderId

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

        The order-ID allocator is intentionally retained. BTS must
        not forget IDs it previously allocated or observed simply
        because the socket connection was interrupted. A future
        nextValidId callback can safely advance the allocator again.
        """

        self._position_request_active = False

        self._api_ready.reset()