"""Determine whether BTS is safe to submit new IB orders."""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.ib_api_ready import IBApiReady
from app.ib_broker_client import IBBrokerClient
from app.ib_order_id_allocator import IBOrderIdAllocator
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls


class IBReadinessStatus(Enum):
    """Overall Interactive Brokers trading-readiness state."""

    READY = "Ready"
    NOT_READY = "NotReady"


class IBReadinessFailure(Enum):
    """Individual condition preventing IB trading readiness."""

    TRADING_PAUSED = "TradingPaused"
    KILL_SWITCH_ACTIVE = "KillSwitchActive"
    API_NOT_READY = "ApiNotReady"
    ORDER_ID_NOT_READY = "OrderIdNotReady"
    POSITION_SNAPSHOT_INCOMPLETE = "PositionSnapshotIncomplete"
    POSITIONS_NOT_RECONCILED = "PositionsNotReconciled"
    EXECUTION_UNCERTAINTY = "ExecutionUncertainty"


@dataclass(frozen=True, slots=True)
class IBTradingReadinessSnapshot:
    """Immutable result of one BTS trading-readiness evaluation."""

    status: IBReadinessStatus
    ready: bool
    trading_paused: bool
    kill_switch_active: bool
    api_ready: bool
    order_id_ready: bool
    position_snapshot_complete: bool
    positions_reconciled: bool
    execution_state_clear: bool
    failures: tuple[IBReadinessFailure, ...]
    reason: str


class IBTradingReadiness:
    """Fail-closed readiness gate immediately before IB execution.

    This component combines existing BTS safety state.

    It does not mutate TradingControls, KillSwitch, broker state,
    or the order-ID allocator.

    Position reconciliation and execution uncertainty are supplied
    explicitly by the surrounding coordinator because those checks
    require broader Eagle/BTS/IB context than this gate owns.
    """

    def __init__(
        self,
        *,
        api_ready: IBApiReady,
        order_id_allocator: IBOrderIdAllocator,
        broker_client: IBBrokerClient,
        trading_controls: TradingControls,
        kill_switch: KillSwitch,
    ) -> None:
        """Create the BTS IB trading-readiness gate."""

        if not isinstance(
            api_ready,
            IBApiReady,
        ):
            raise TypeError(
                "'api_ready' must be an IBApiReady."
            )

        if not isinstance(
            order_id_allocator,
            IBOrderIdAllocator,
        ):
            raise TypeError(
                "'order_id_allocator' must be an "
                "IBOrderIdAllocator."
            )

        if not isinstance(
            broker_client,
            IBBrokerClient,
        ):
            raise TypeError(
                "'broker_client' must be an IBBrokerClient."
            )

        if not isinstance(
            trading_controls,
            TradingControls,
        ):
            raise TypeError(
                "'trading_controls' must be a TradingControls."
            )

        if not isinstance(
            kill_switch,
            KillSwitch,
        ):
            raise TypeError(
                "'kill_switch' must be a KillSwitch."
            )

        self._api_ready = api_ready
        self._order_id_allocator = order_id_allocator
        self._broker_client = broker_client
        self._trading_controls = trading_controls
        self._kill_switch = kill_switch

    @property
    def api_ready(self) -> IBApiReady:
        """Return the IB API readiness tracker."""

        return self._api_ready

    @property
    def order_id_allocator(
        self,
    ) -> IBOrderIdAllocator:
        """Return the IB order-ID allocator."""

        return self._order_id_allocator

    @property
    def broker_client(self) -> IBBrokerClient:
        """Return the BTS IB broker client."""

        return self._broker_client

    @property
    def trading_controls(
        self,
    ) -> TradingControls:
        """Return trader-controlled runtime settings."""

        return self._trading_controls

    @property
    def kill_switch(self) -> KillSwitch:
        """Return the BTS emergency kill switch."""

        return self._kill_switch

    def evaluate(
        self,
        *,
        positions_reconciled: bool,
        execution_state_clear: bool,
    ) -> IBTradingReadinessSnapshot:
        """Evaluate whether BTS may submit a new IB order.

        Args:
            positions_reconciled:
                True only after the surrounding reconciliation
                system has established that Eagle/BTS/broker
                position state agrees.

            execution_state_clear:
                True only when there is no unresolved execution
                uncertainty requiring investigation or recovery.

        Returns:
            An immutable readiness snapshot.

        The method is deliberately fail-closed. Every required
        safety condition must be satisfied before ``ready`` is True.
        """

        normalized_positions_reconciled = (
            self._validate_bool(
                positions_reconciled,
                "positions_reconciled",
            )
        )

        normalized_execution_state_clear = (
            self._validate_bool(
                execution_state_clear,
                "execution_state_clear",
            )
        )

        trading_paused = (
            self._trading_controls.is_paused
        )

        kill_switch_active = (
            self._kill_switch.active
        )

        api_ready = (
            self._api_ready.ready
        )

        order_id_ready = (
            self._order_id_allocator.initialized
            and self._order_id_allocator.next_order_id
            is not None
        )

        position_snapshot_complete = (
            self._broker_client.snapshot_complete
        )

        failures: list[
            IBReadinessFailure
        ] = []

        if trading_paused:
            failures.append(
                IBReadinessFailure.TRADING_PAUSED
            )

        if kill_switch_active:
            failures.append(
                IBReadinessFailure.KILL_SWITCH_ACTIVE
            )

        if not api_ready:
            failures.append(
                IBReadinessFailure.API_NOT_READY
            )

        if not order_id_ready:
            failures.append(
                IBReadinessFailure.ORDER_ID_NOT_READY
            )

        if not position_snapshot_complete:
            failures.append(
                IBReadinessFailure.POSITION_SNAPSHOT_INCOMPLETE
            )

        if not normalized_positions_reconciled:
            failures.append(
                IBReadinessFailure.POSITIONS_NOT_RECONCILED
            )

        if not normalized_execution_state_clear:
            failures.append(
                IBReadinessFailure.EXECUTION_UNCERTAINTY
            )

        ready = (
            len(
                failures
            )
            == 0
        )

        if ready:
            status = (
                IBReadinessStatus.READY
            )

            reason = (
                "All IB trading-readiness conditions "
                "are satisfied."
            )

        else:
            status = (
                IBReadinessStatus.NOT_READY
            )

            failure_text = ", ".join(
                failure.value
                for failure in failures
            )

            reason = (
                "IB trading is blocked by: "
                f"{failure_text}."
            )

        return IBTradingReadinessSnapshot(
            status=status,
            ready=ready,
            trading_paused=trading_paused,
            kill_switch_active=kill_switch_active,
            api_ready=api_ready,
            order_id_ready=order_id_ready,
            position_snapshot_complete=(
                position_snapshot_complete
            ),
            positions_reconciled=(
                normalized_positions_reconciled
            ),
            execution_state_clear=(
                normalized_execution_state_clear
            ),
            failures=tuple(
                failures
            ),
            reason=reason,
        )

    def require_ready(
        self,
        *,
        positions_reconciled: bool,
        execution_state_clear: bool,
    ) -> IBTradingReadinessSnapshot:
        """Require readiness or raise before broker submission.

        Returns the successful readiness snapshot when every
        condition is satisfied.

        Raises:
            RuntimeError:
                If one or more readiness conditions fail.
        """

        result = self.evaluate(
            positions_reconciled=positions_reconciled,
            execution_state_clear=execution_state_clear,
        )

        if not result.ready:
            raise RuntimeError(
                result.reason
            )

        return result

    @staticmethod
    def _validate_bool(
        value: Any,
        field_name: str,
    ) -> bool:
        """Validate an explicit readiness boolean."""

        if not isinstance(
            value,
            bool,
        ):
            raise TypeError(
                f"'{field_name}' must be a bool."
            )

        return value