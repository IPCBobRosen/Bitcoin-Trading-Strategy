"""Coordinate safe BTS order submission to Interactive Brokers."""

from collections.abc import Callable
from dataclasses import dataclass

from ibapi.contract import Contract
from ibapi.order import Order

from app.communications.trade_request import TradeRequest
from app.duplicate_order_guard import DuplicateOrderGuard
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
)
from app.ib_order_factory import (
    IBOrderFactory,
    IBOrderPackage,
)


@dataclass(frozen=True, slots=True)
class IBSubmissionResult:
    """Immutable result of one BTS-to-IB submission attempt."""

    event_id: str
    broker_order_id: int
    package: IBOrderPackage
    ledger_record: ExecutionRecord


class IBExecutionClient:
    """Safely coordinate BTS execution state with IB order submission.

    This class does not require a live IB connection.

    The actual broker call is injected as ``place_order_function``.
    During tests this is a mock. Later, it can be connected to the
    official IB API application's placeOrder method.

    Submission sequence:

        1. Validate the TradeRequest.
        2. Reject an event already known durably.
        3. Reserve the event in the in-memory duplicate guard.
        4. Reserve the event in the durable execution ledger.
        5. Construct the IB Contract and Order.
        6. Persist SUBMITTED with the intended IB order ID.
        7. Invoke the broker place-order function.

    Step 6 intentionally occurs before step 7.

    If BTS crashes while or immediately after calling the broker,
    the durable ledger therefore records that the order may have
    reached IB. Recovery must reconcile that order with IB instead
    of blindly submitting the Eagle event again.
    """

    def __init__(
        self,
        *,
        order_factory: IBOrderFactory,
        duplicate_guard: DuplicateOrderGuard,
        execution_ledger: ExecutionLedger,
        place_order_function: Callable[
            [int, Contract, Order],
            None,
        ],
    ) -> None:
        """Create an IB execution coordinator."""

        if not isinstance(
            order_factory,
            IBOrderFactory,
        ):
            raise TypeError(
                "'order_factory' must be an IBOrderFactory."
            )

        if not isinstance(
            duplicate_guard,
            DuplicateOrderGuard,
        ):
            raise TypeError(
                "'duplicate_guard' must be a DuplicateOrderGuard."
            )

        if not isinstance(
            execution_ledger,
            ExecutionLedger,
        ):
            raise TypeError(
                "'execution_ledger' must be an ExecutionLedger."
            )

        if not callable(
            place_order_function
        ):
            raise TypeError(
                "'place_order_function' must be callable."
            )

        self._order_factory = order_factory
        self._duplicate_guard = duplicate_guard
        self._execution_ledger = execution_ledger
        self._place_order_function = place_order_function

    @property
    def order_factory(self) -> IBOrderFactory:
        """Return the configured IB order factory."""

        return self._order_factory

    @property
    def duplicate_guard(
        self,
    ) -> DuplicateOrderGuard:
        """Return the in-memory duplicate-order guard."""

        return self._duplicate_guard

    @property
    def execution_ledger(
        self,
    ) -> ExecutionLedger:
        """Return the durable execution ledger."""

        return self._execution_ledger

    def submit(
        self,
        trade_request: TradeRequest,
        *,
        contract_month: str,
        broker_order_id: int,
    ) -> IBSubmissionResult:
        """Safely submit one approved TradeRequest toward IB.

        The supplied broker_order_id will eventually come from the
        IB order-ID allocator driven by nextValidId.

        Raises:
            TypeError:
                If trade_request has the wrong type.

            ValueError:
                If the broker order ID is invalid or the Eagle event
                has already been processed.

            Exception:
                Any exception raised by the injected broker function
                is propagated. The ledger remains SUBMITTED because
                the broker outcome is then uncertain.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        normalized_order_id = (
            self._validate_broker_order_id(
                broker_order_id
            )
        )

        event_id = trade_request.event_id

        if self._execution_ledger.contains(
            event_id
        ):
            raise ValueError(
                f"Eagle event {event_id!r} already exists "
                "in the execution ledger."
            )

        duplicate_decision = (
            self._duplicate_guard.reserve(
                trade_request
            )
        )

        if not duplicate_decision.allowed:
            raise ValueError(
                duplicate_decision.reason
            )

        try:
            reserved_record = (
                self._execution_ledger.reserve(
                    trade_request
                )
            )

        except Exception:
            self._duplicate_guard.release(
                event_id
            )
            raise

        try:
            package = self._order_factory.create(
                trade_request,
                contract_month=contract_month,
            )

        except Exception:
            self._execution_ledger.mark_rejected(
                event_id,
                reason=(
                    "IB order construction failed before "
                    "broker submission."
                ),
            )

            raise

        submitted_record = (
            self._execution_ledger.mark_submitted(
                event_id,
                broker_order_id=normalized_order_id,
            )
        )

        self._place_order_function(
            normalized_order_id,
            package.contract,
            package.order,
        )

        return IBSubmissionResult(
            event_id=event_id,
            broker_order_id=normalized_order_id,
            package=package,
            ledger_record=submitted_record,
        )

    @staticmethod
    def _validate_broker_order_id(
        value: int,
    ) -> int:
        """Validate an IB API order identifier."""

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(
                "'broker_order_id' must be a "
                "non-negative integer."
            )

        return value