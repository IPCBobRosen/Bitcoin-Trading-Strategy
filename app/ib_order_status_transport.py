"""Translate IBKR order callbacks into durable BTS execution state."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
    ExecutionStatus,
)


class IBStatusOutcome(Enum):
    """Outcome of processing one IBKR order-status callback."""

    UPDATED = "Updated"
    NO_CHANGE = "NoChange"
    IGNORED = "Ignored"


@dataclass(frozen=True, slots=True)
class IBStatusResult:
    """Immutable result of processing one IBKR callback."""

    outcome: IBStatusOutcome
    broker_order_id: int
    ib_status: str
    execution_record: ExecutionRecord
    reason: str

    @property
    def changed(self) -> bool:
        """Return True when durable execution state changed."""

        return self.outcome is IBStatusOutcome.UPDATED


class IBOrderStatusTransport:
    """Map Interactive Brokers callbacks into ExecutionLedger state.

    IBKR may send duplicate orderStatus callbacks and may omit some
    intermediate callbacks. This transport is therefore idempotent
    and permits valid forward progress without requiring every
    intermediate state to appear.

    Broker-order lookup is performed against the durable ledger.
    """

    _ACKNOWLEDGED_IB_STATUSES = {
        "PENDINGSUBMIT",
        "PRESUBMITTED",
        "SUBMITTED",
    }

    _CANCELLED_IB_STATUSES = {
        "CANCELLED",
        "APICANCELLED",
    }

    _NON_FINAL_INFORMATIONAL_STATUSES = {
        "PENDINGCANCEL",
        "PRECANCELLED",
        "INACTIVE",
    }

    def __init__(
        self,
        execution_ledger: ExecutionLedger,
    ) -> None:
        """Create an IB order-status callback transport."""

        if not isinstance(
            execution_ledger,
            ExecutionLedger,
        ):
            raise TypeError(
                "'execution_ledger' must be an ExecutionLedger."
            )

        self._execution_ledger = execution_ledger

    @property
    def execution_ledger(
        self,
    ) -> ExecutionLedger:
        """Return the durable execution ledger."""

        return self._execution_ledger

    def handle_order_status(
        self,
        *,
        broker_order_id: int,
        status: str,
        filled: Decimal | int | float | str,
        remaining: Decimal | int | float | str,
    ) -> IBStatusResult:
        """Process one IBKR orderStatus callback.

        Args:
            broker_order_id:
                IB API order identifier.

            status:
                Raw IB order-status text.

            filled:
                Quantity IB reports as filled.

            remaining:
                Quantity IB reports as remaining.

        Returns:
            An IBStatusResult describing whether durable state
            changed.

        Raises:
            KeyError:
                If BTS has no durable execution associated with the
                broker order ID.
        """

        normalized_order_id = (
            self._validate_broker_order_id(
                broker_order_id
            )
        )

        normalized_status = (
            self._validate_status(
                status
            )
        )

        normalized_filled = (
            self._validate_quantity(
                filled,
                "filled",
            )
        )

        normalized_remaining = (
            self._validate_quantity(
                remaining,
                "remaining",
            )
        )

        record = self._find_record_by_broker_order_id(
            normalized_order_id
        )

        if record is None:
            raise KeyError(
                f"No BTS execution record exists for "
                f"broker order ID {normalized_order_id}."
            )

        desired_status = (
            self._determine_execution_status(
                ib_status=normalized_status,
                filled=normalized_filled,
                remaining=normalized_remaining,
            )
        )

        if desired_status is None:
            return IBStatusResult(
                outcome=IBStatusOutcome.IGNORED,
                broker_order_id=normalized_order_id,
                ib_status=normalized_status,
                execution_record=record,
                reason=(
                    f"IB status {normalized_status!r} does not "
                    "represent a durable BTS execution transition."
                ),
            )

        if record.status is desired_status:
            return IBStatusResult(
                outcome=IBStatusOutcome.NO_CHANGE,
                broker_order_id=normalized_order_id,
                ib_status=normalized_status,
                execution_record=record,
                reason=(
                    "Duplicate IB callback produced no "
                    "execution-state change."
                ),
            )

        if self._is_stale_or_regressive(
            current_status=record.status,
            desired_status=desired_status,
        ):
            return IBStatusResult(
                outcome=IBStatusOutcome.IGNORED,
                broker_order_id=normalized_order_id,
                ib_status=normalized_status,
                execution_record=record,
                reason=(
                    "IB callback would regress an already "
                    "advanced BTS execution state."
                ),
            )

        updated = self._apply_transition(
            record,
            desired_status,
        )

        return IBStatusResult(
            outcome=IBStatusOutcome.UPDATED,
            broker_order_id=normalized_order_id,
            ib_status=normalized_status,
            execution_record=updated,
            reason=(
                f"BTS execution state advanced to "
                f"{updated.status.value}."
            ),
        )

    def handle_rejection(
        self,
        *,
        broker_order_id: int,
        reason: str,
    ) -> IBStatusResult:
        """Record a confirmed broker/order rejection.

        This method intentionally requires an explicit rejection
        signal. Generic IB error callbacks should not automatically
        be interpreted as order rejection because many IB error
        messages are informational or unrelated to order validity.
        """

        normalized_order_id = (
            self._validate_broker_order_id(
                broker_order_id
            )
        )

        normalized_reason = (
            self._validate_reason(
                reason
            )
        )

        record = self._find_record_by_broker_order_id(
            normalized_order_id
        )

        if record is None:
            raise KeyError(
                f"No BTS execution record exists for "
                f"broker order ID {normalized_order_id}."
            )

        if (
            record.status
            is ExecutionStatus.REJECTED
        ):
            return IBStatusResult(
                outcome=IBStatusOutcome.NO_CHANGE,
                broker_order_id=normalized_order_id,
                ib_status="REJECTED",
                execution_record=record,
                reason=(
                    "Duplicate broker rejection produced "
                    "no execution-state change."
                ),
            )

        if record.terminal:
            return IBStatusResult(
                outcome=IBStatusOutcome.IGNORED,
                broker_order_id=normalized_order_id,
                ib_status="REJECTED",
                execution_record=record,
                reason=(
                    "Broker rejection was ignored because "
                    "the BTS execution is already terminal."
                ),
            )

        updated = (
            self._execution_ledger.mark_rejected(
                record.event_id,
                reason=normalized_reason,
            )
        )

        return IBStatusResult(
            outcome=IBStatusOutcome.UPDATED,
            broker_order_id=normalized_order_id,
            ib_status="REJECTED",
            execution_record=updated,
            reason=(
                "Confirmed broker rejection was persisted."
            ),
        )

    def _find_record_by_broker_order_id(
        self,
        broker_order_id: int,
    ) -> ExecutionRecord | None:
        """Find one durable BTS record by IB order ID."""

        matches = tuple(
            record
            for record
            in self._execution_ledger.all_records()
            if record.broker_order_id
            == broker_order_id
        )

        if not matches:
            return None

        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple BTS execution records use "
                f"broker order ID {broker_order_id}."
            )

        return matches[0]

    @classmethod
    def _determine_execution_status(
        cls,
        *,
        ib_status: str,
        filled: Decimal,
        remaining: Decimal,
    ) -> ExecutionStatus | None:
        """Translate IB status/quantities into BTS execution state."""

        compact_status = (
            ib_status.replace(
                "_",
                "",
            )
            .replace(
                " ",
                "",
            )
            .upper()
        )

        if compact_status == "FILLED":
            return ExecutionStatus.FILLED

        if (
            compact_status
            in cls._CANCELLED_IB_STATUSES
        ):
            return ExecutionStatus.CANCELLED

        if (
            filled > 0
            and remaining > 0
        ):
            return ExecutionStatus.PARTIALLY_FILLED

        if (
            compact_status
            in cls._ACKNOWLEDGED_IB_STATUSES
        ):
            return ExecutionStatus.ACKNOWLEDGED

        if (
            compact_status
            in cls._NON_FINAL_INFORMATIONAL_STATUSES
        ):
            return None

        return None

    @staticmethod
    def _is_stale_or_regressive(
        *,
        current_status: ExecutionStatus,
        desired_status: ExecutionStatus,
    ) -> bool:
        """Return True for callbacks that would move state backward."""

        rank = {
            ExecutionStatus.RESERVED: 0,
            ExecutionStatus.SUBMITTED: 1,
            ExecutionStatus.ACKNOWLEDGED: 2,
            ExecutionStatus.PARTIALLY_FILLED: 3,
            ExecutionStatus.FILLED: 4,
        }

        if current_status in {
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }:
            return True

        if desired_status in {
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }:
            return False

        current_rank = rank.get(
            current_status
        )

        desired_rank = rank.get(
            desired_status
        )

        if (
            current_rank is None
            or desired_rank is None
        ):
            return False

        return (
            desired_rank
            < current_rank
        )

    def _apply_transition(
        self,
        record: ExecutionRecord,
        desired_status: ExecutionStatus,
    ) -> ExecutionRecord:
        """Persist one validated forward execution transition."""

        event_id = record.event_id

        if (
            desired_status
            is ExecutionStatus.ACKNOWLEDGED
        ):
            return (
                self._execution_ledger.mark_acknowledged(
                    event_id
                )
            )

        if (
            desired_status
            is ExecutionStatus.PARTIALLY_FILLED
        ):
            return (
                self._execution_ledger.mark_partially_filled(
                    event_id
                )
            )

        if (
            desired_status
            is ExecutionStatus.FILLED
        ):
            return (
                self._execution_ledger.mark_filled(
                    event_id
                )
            )

        if (
            desired_status
            is ExecutionStatus.CANCELLED
        ):
            return (
                self._execution_ledger.mark_cancelled(
                    event_id,
                    reason="IB confirmed order cancellation.",
                )
            )

        raise ValueError(
            f"Unsupported desired execution status: "
            f"{desired_status.value}."
        )

    @staticmethod
    def _validate_broker_order_id(
        value: Any,
    ) -> int:
        """Validate an IB order identifier."""

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

    @staticmethod
    def _validate_status(
        value: Any,
    ) -> str:
        """Validate and normalize IB status text."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                "'status' must be a non-empty string."
            )

        return value.strip()

    @staticmethod
    def _validate_quantity(
        value: Any,
        field_name: str,
    ) -> Decimal:
        """Validate an IB callback quantity."""

        if isinstance(
            value,
            bool,
        ):
            raise ValueError(
                f"'{field_name}' must be a "
                "non-negative finite number."
            )

        try:
            normalized = Decimal(
                str(value)
            )

        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            raise ValueError(
                f"'{field_name}' must be a "
                "non-negative finite number."
            ) from error

        if (
            not normalized.is_finite()
            or normalized < 0
        ):
            raise ValueError(
                f"'{field_name}' must be a "
                "non-negative finite number."
            )

        return normalized

    @staticmethod
    def _validate_reason(
        value: Any,
    ) -> str:
        """Validate a confirmed rejection reason."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                "'reason' must be a non-empty string."
            )

        return value.strip()