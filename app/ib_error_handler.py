"""Classify and safely handle Interactive Brokers API messages."""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
    ExecutionStatus,
)
from app.kill_switch import KillSwitch


class IBErrorSeverity(Enum):
    """Safety classification for one IB API message."""

    INFORMATION = "Information"
    WARNING = "Warning"
    ORDER_REJECTED = "OrderRejected"
    ORDER_CANCELLED = "OrderCancelled"
    CONNECTION_LOST = "ConnectionLost"
    CONNECTION_RESTORED = "ConnectionRestored"
    CRITICAL = "Critical"
    UNKNOWN = "Unknown"


@dataclass(frozen=True, slots=True)
class IBErrorResult:
    """Immutable result of processing one IB API message."""

    request_id: int
    error_code: int
    message: str
    severity: IBErrorSeverity
    trading_blocked: bool
    execution_record: ExecutionRecord | None
    reason: str


class IBErrorHandler:
    """Interpret IB API error, warning, and notification messages.

    Interactive Brokers delivers informational messages, warnings,
    connectivity events, and genuine errors through EWrapper.error().

    BTS therefore classifies the numeric IB code before taking any
    safety action.

    Critical infrastructure conditions activate the KillSwitch.
    Restoration notifications never automatically reset an active
    KillSwitch. Operator intervention and reconciliation are required
    before trading is resumed.
    """

    _INFORMATION_CODES = {
        2104,
        2106,
        2107,
        2108,
        2158,
    }

    _WARNING_CODES = {
        2100,
        2101,
        2102,
        2103,
        2105,
        2109,
        2137,
        2168,
        2169,
    }

    _CONNECTION_LOST_CODES = {
        1100,
        2110,
    }

    _CONNECTION_RESTORED_CODES = {
        1101,
        1102,
    }

    _CRITICAL_CODES = {
        100,
        103,
        1300,
        502,
        503,
        504,
        507,
        10015,
    }

    _ORDER_REJECTION_CODE = 201
    _ORDER_CANCELLATION_CODE = 202

    def __init__(
        self,
        *,
        execution_ledger: ExecutionLedger,
        kill_switch: KillSwitch,
    ) -> None:
        """Create an IB API error-safety handler."""

        if not isinstance(
            execution_ledger,
            ExecutionLedger,
        ):
            raise TypeError(
                "'execution_ledger' must be an ExecutionLedger."
            )

        if not isinstance(
            kill_switch,
            KillSwitch,
        ):
            raise TypeError(
                "'kill_switch' must be a KillSwitch."
            )

        self._execution_ledger = execution_ledger
        self._kill_switch = kill_switch

    @property
    def execution_ledger(
        self,
    ) -> ExecutionLedger:
        """Return the durable BTS execution ledger."""

        return self._execution_ledger

    @property
    def kill_switch(
        self,
    ) -> KillSwitch:
        """Return the BTS emergency kill switch."""

        return self._kill_switch

    def handle(
        self,
        *,
        request_id: int,
        error_code: int,
        message: str,
    ) -> IBErrorResult:
        """Process one IB EWrapper.error message."""

        normalized_request_id = (
            self._validate_request_id(
                request_id
            )
        )

        normalized_error_code = (
            self._validate_error_code(
                error_code
            )
        )

        normalized_message = (
            self._validate_message(
                message
            )
        )

        if (
            normalized_error_code
            in self._INFORMATION_CODES
        ):
            return self._result(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
                severity=IBErrorSeverity.INFORMATION,
                execution_record=None,
                reason=(
                    "IB informational notification; "
                    "no BTS safety action required."
                ),
            )

        if (
            normalized_error_code
            in self._CONNECTION_RESTORED_CODES
        ):
            return self._result(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
                severity=IBErrorSeverity.CONNECTION_RESTORED,
                execution_record=None,
                reason=(
                    "IB connectivity was restored. "
                    "Any active BTS kill switch remains active "
                    "until explicit operator reset."
                ),
            )

        if (
            normalized_error_code
            in self._CONNECTION_LOST_CODES
        ):
            self._activate_kill_switch(
                error_code=normalized_error_code,
                message=normalized_message,
            )

            return self._result(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
                severity=IBErrorSeverity.CONNECTION_LOST,
                execution_record=None,
                reason=(
                    "IB connectivity loss requires BTS "
                    "trading to remain blocked."
                ),
            )

        if (
            normalized_error_code
            in self._CRITICAL_CODES
        ):
            self._activate_kill_switch(
                error_code=normalized_error_code,
                message=normalized_message,
            )

            return self._result(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
                severity=IBErrorSeverity.CRITICAL,
                execution_record=None,
                reason=(
                    "Critical IB API condition activated "
                    "the BTS kill switch."
                ),
            )

        if (
            normalized_error_code
            == self._ORDER_REJECTION_CODE
        ):
            return self._handle_order_rejection(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
            )

        if (
            normalized_error_code
            == self._ORDER_CANCELLATION_CODE
        ):
            return self._handle_order_cancellation(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
            )

        if (
            normalized_error_code
            in self._WARNING_CODES
        ):
            return self._result(
                request_id=normalized_request_id,
                error_code=normalized_error_code,
                message=normalized_message,
                severity=IBErrorSeverity.WARNING,
                execution_record=None,
                reason=(
                    "IB warning recorded without automatically "
                    "changing durable BTS execution state."
                ),
            )

        return self._result(
            request_id=normalized_request_id,
            error_code=normalized_error_code,
            message=normalized_message,
            severity=IBErrorSeverity.UNKNOWN,
            execution_record=None,
            reason=(
                "Unclassified IB message recorded without "
                "automatic execution-state mutation."
            ),
        )

    def _handle_order_rejection(
        self,
        *,
        request_id: int,
        error_code: int,
        message: str,
    ) -> IBErrorResult:
        """Persist an explicit IB order rejection when it is ours."""

        record = (
            self._find_record_by_broker_order_id(
                request_id
            )
        )

        if record is None:
            return self._result(
                request_id=request_id,
                error_code=error_code,
                message=message,
                severity=IBErrorSeverity.ORDER_REJECTED,
                execution_record=None,
                reason=(
                    "IB rejected an order that is not represented "
                    "in the BTS execution ledger."
                ),
            )

        if (
            record.status
            is ExecutionStatus.REJECTED
        ):
            return self._result(
                request_id=request_id,
                error_code=error_code,
                message=message,
                severity=IBErrorSeverity.ORDER_REJECTED,
                execution_record=record,
                reason=(
                    "Duplicate IB rejection produced no "
                    "BTS execution-state change."
                ),
            )

        if record.terminal:
            return self._result(
                request_id=request_id,
                error_code=error_code,
                message=message,
                severity=IBErrorSeverity.ORDER_REJECTED,
                execution_record=record,
                reason=(
                    "IB rejection arrived after the BTS execution "
                    "had already reached a terminal state."
                ),
            )

        updated = (
            self._execution_ledger.mark_rejected(
                record.event_id,
                reason=(
                    f"IB error {error_code}: {message}"
                ),
            )
        )

        return self._result(
            request_id=request_id,
            error_code=error_code,
            message=message,
            severity=IBErrorSeverity.ORDER_REJECTED,
            execution_record=updated,
            reason=(
                "Confirmed IB order rejection was persisted "
                "to the BTS execution ledger."
            ),
        )

    def _handle_order_cancellation(
        self,
        *,
        request_id: int,
        error_code: int,
        message: str,
    ) -> IBErrorResult:
        """Persist an explicit IB cancellation when it is ours."""

        record = (
            self._find_record_by_broker_order_id(
                request_id
            )
        )

        if record is None:
            return self._result(
                request_id=request_id,
                error_code=error_code,
                message=message,
                severity=IBErrorSeverity.ORDER_CANCELLED,
                execution_record=None,
                reason=(
                    "IB cancelled an order that is not represented "
                    "in the BTS execution ledger."
                ),
            )

        if (
            record.status
            is ExecutionStatus.CANCELLED
        ):
            return self._result(
                request_id=request_id,
                error_code=error_code,
                message=message,
                severity=IBErrorSeverity.ORDER_CANCELLED,
                execution_record=record,
                reason=(
                    "Duplicate IB cancellation produced no "
                    "BTS execution-state change."
                ),
            )

        if record.terminal:
            return self._result(
                request_id=request_id,
                error_code=error_code,
                message=message,
                severity=IBErrorSeverity.ORDER_CANCELLED,
                execution_record=record,
                reason=(
                    "IB cancellation arrived after the BTS execution "
                    "had already reached a terminal state."
                ),
            )

        updated = (
            self._execution_ledger.mark_cancelled(
                record.event_id,
                reason=(
                    f"IB error {error_code}: {message}"
                ),
            )
        )

        return self._result(
            request_id=request_id,
            error_code=error_code,
            message=message,
            severity=IBErrorSeverity.ORDER_CANCELLED,
            execution_record=updated,
            reason=(
                "Confirmed IB order cancellation was persisted "
                "to the BTS execution ledger."
            ),
        )

    def _find_record_by_broker_order_id(
        self,
        broker_order_id: int,
    ) -> ExecutionRecord | None:
        """Find exactly one BTS execution by IB order ID."""

        if broker_order_id < 0:
            return None

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
            self._kill_switch.activate(
                "Multiple BTS execution records use "
                f"IB broker order ID {broker_order_id}."
            )

            raise RuntimeError(
                f"Multiple BTS execution records use "
                f"broker order ID {broker_order_id}."
            )

        return matches[0]

    def _activate_kill_switch(
        self,
        *,
        error_code: int,
        message: str,
    ) -> None:
        """Activate BTS emergency protection for an IB failure."""

        self._kill_switch.activate(
            f"IB error {error_code}: {message}"
        )

    def _result(
        self,
        *,
        request_id: int,
        error_code: int,
        message: str,
        severity: IBErrorSeverity,
        execution_record: ExecutionRecord | None,
        reason: str,
    ) -> IBErrorResult:
        """Build one immutable handler result."""

        return IBErrorResult(
            request_id=request_id,
            error_code=error_code,
            message=message,
            severity=severity,
            trading_blocked=self._kill_switch.active,
            execution_record=execution_record,
            reason=reason,
        )

    @staticmethod
    def _validate_request_id(
        value: Any,
    ) -> int:
        """Validate an IB request/order ID.

        IB uses -1 for messages that are not associated with a
        specific request or order.
        """

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < -1
        ):
            raise ValueError(
                "'request_id' must be an integer "
                "greater than or equal to -1."
            )

        return value

    @staticmethod
    def _validate_error_code(
        value: Any,
    ) -> int:
        """Validate an IB numeric error code."""

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(
                "'error_code' must be a non-negative integer."
            )

        return value

    @staticmethod
    def _validate_message(
        value: Any,
    ) -> str:
        """Validate and normalize an IB error message."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                "'message' must be a non-empty string."
            )

        return value.strip()