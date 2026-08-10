"""Interactive Brokers API app for BTS position snapshots."""

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

from app.ib_api_ready import IBApiReady
from app.ib_broker_client import IBBrokerClient
from app.ib_position_transport import IBPositionTransport


class IBApiPositionApp(EWrapper, EClient):
    """IBKR EWrapper/EClient bridge for position reconciliation only."""

    def __init__(
        self,
        broker_client: IBBrokerClient,
    ) -> None:
        """Create a position-only IBKR API application."""

        if not isinstance(
            broker_client,
            IBBrokerClient,
        ):
            raise TypeError(
                "'broker_client' must be an IBBrokerClient."
            )

        EWrapper.__init__(self)
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
        """Receive IBKR handshake confirmation.

        BTS uses this callback only as proof that the IB API
        connection negotiation has completed.
        """

        self._api_ready.record_next_valid_id(
            orderId
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
        """Reset local API state if the IBKR connection closes."""

        self._position_request_active = False

        self._api_ready.reset()