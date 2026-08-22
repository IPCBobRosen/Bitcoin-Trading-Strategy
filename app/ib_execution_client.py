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
    ExecutionStatus,
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

    Normal submission sequence:

        1. Validate the TradeRequest.
        2. Reject an event already known durably.
        3. Reserve the event in the in-memory duplicate guard.
        4. Reserve the event in the durable execution ledger.
        5. Construct the IB Contract and Order.
        6. Persist SUBMITTED with the intended IB order ID.
        7. Invoke the broker place-order function.

    Recovery-capable submission separates steps 3-4 from steps 5-7:

        reserve_execution()
            -> durable RESERVED
            -> definitely not sent to IB

        submit_reserved()
            -> require exact existing RESERVED event
            -> construct order
            -> persist SUBMITTED
            -> invoke broker

    SUBMITTED is persisted before the broker call.

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

    def reserve_execution(
        self,
        trade_request: TradeRequest,
    ) -> ExecutionRecord:
        """Durably reserve an execution without submitting to IB.

        RESERVED means BTS knows the execution obligation exists,
        but no broker order has been submitted.

        This is intentionally separate from submit_reserved() so an
        actionable risk-reducing exit can survive a broker outage
        without being lost.

        Raises:
            TypeError:
                If trade_request has the wrong type.

            ValueError:
                If the event is already known durably or the
                duplicate guard rejects it.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
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

        return reserved_record

    def submit_reserved(
        self,
        trade_request: TradeRequest,
        *,
        contract_month: str,
        broker_order_id: int,
    ) -> IBSubmissionResult:
        """Submit one exact existing RESERVED execution toward IB.

        The durable execution must already exist in RESERVED state.

        This method never creates a second durable execution record.
        It advances the existing reservation from RESERVED to
        SUBMITTED immediately before calling the broker.

        A restarted process may have an empty in-memory duplicate
        guard even though the durable RESERVED record survives.
        In that case the event is restored into the in-memory guard
        before broker submission.

        Once an event has advanced beyond RESERVED, this method
        refuses to submit it again.
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

        existing_record = (
            self._execution_ledger.get(
                event_id
            )
        )

        if existing_record is None:
            raise ValueError(
                f"Eagle event {event_id!r} has no "
                "existing RESERVED execution."
            )

        if (
            existing_record.status
            is not ExecutionStatus.RESERVED
        ):
            raise ValueError(
                f"Eagle event {event_id!r} must be "
                "RESERVED before submit_reserved(). "
                f"Current status: "
                f"{existing_record.status.value}."
            )

        self._require_reserved_record_matches_request(
            record=existing_record,
            trade_request=trade_request,
        )

        if not self._duplicate_guard.contains(
            event_id
        ):
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

    def submit(
        self,
        trade_request: TradeRequest,
        *,
        contract_month: str,
        broker_order_id: int,
    ) -> IBSubmissionResult:
        """Safely submit one new approved TradeRequest toward IB.

        Normal submissions are first durably RESERVED and then
        immediately advanced through submit_reserved().

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

        self.reserve_execution(
            trade_request
        )

        return self.submit_reserved(
            trade_request,
            contract_month=contract_month,
            broker_order_id=normalized_order_id,
        )

    @staticmethod
    def _require_reserved_record_matches_request(
        *,
        record: ExecutionRecord,
        trade_request: TradeRequest,
    ) -> None:
        """Require an existing reservation to match the request identity."""

        if not isinstance(
            record,
            ExecutionRecord,
        ):
            raise TypeError(
                "'record' must be an ExecutionRecord."
            )

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        mismatches: list[str] = []

        if record.event_id != trade_request.event_id:
            mismatches.append("event_id")

        if record.signal_id != trade_request.signal_id:
            mismatches.append("signal_id")

        if (
            record.symbol.strip().upper()
            != trade_request.symbol.strip().upper()
        ):
            mismatches.append("symbol")

        if record.intent != trade_request.intent.value:
            mismatches.append("intent")

        if record.quantity != trade_request.quantity:
            mismatches.append("quantity")

        if mismatches:
            mismatch_text = ", ".join(
                mismatches
            )

            raise ValueError(
                "Existing RESERVED execution does not match "
                "the supplied TradeRequest. "
                f"Mismatched fields: {mismatch_text}."
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