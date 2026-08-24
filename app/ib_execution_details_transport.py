"""Process IBKR execution details into durable BTS execution state."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import sqlite3
from typing import Any

from ibapi.contract import Contract
from ibapi.execution import Execution

from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
    ExecutionStatus,
)


class IBExecutionDetailsOutcome(Enum):
    """Outcome of processing one IB execution callback."""

    UPDATED = "Updated"
    NO_CHANGE = "NoChange"
    DUPLICATE = "Duplicate"


@dataclass(frozen=True, slots=True)
class IBExecutionDetailsResult:
    """Immutable result of processing one IB execution."""

    outcome: IBExecutionDetailsOutcome
    broker_order_id: int
    exec_id: str
    cumulative_quantity: Decimal
    execution_record: ExecutionRecord
    reason: str

    @property
    def changed(self) -> bool:
        """Return True when BTS durable execution state advanced."""

        return (
            self.outcome
            is IBExecutionDetailsOutcome.UPDATED
        )


class IBExecutionDetailsTransport:
    """Translate IB execDetails callbacks into BTS execution state.

    The transport uses IB's cumulative execution quantity rather
    than summing execution callbacks.

    Each processed IB execId is stored durably in SQLite so replayed
    or repeated execution callbacks remain idempotent even across a
    BTS restart.
    """

    def __init__(
        self,
        execution_ledger: ExecutionLedger,
    ) -> None:
        """Create an IB execution-details transport."""

        if not isinstance(
            execution_ledger,
            ExecutionLedger,
        ):
            raise TypeError(
                "'execution_ledger' must be an ExecutionLedger."
            )

        self._execution_ledger = execution_ledger

        self._initialize_execution_table()

    @property
    def execution_ledger(
        self,
    ) -> ExecutionLedger:
        """Return the durable BTS execution ledger."""

        return self._execution_ledger

    def handle_execution(
        self,
        *,
        contract: Contract,
        execution: Execution,
    ) -> IBExecutionDetailsResult:
        """Process one official IBKR execDetails execution.

        The callback is validated against the durable BTS order:

        - broker order ID must be known;
        - contract symbol must match;
        - BUY/SELL execution side must match BTS intent;
        - execution quantities must be valid;
        - cumulative quantity may not exceed requested quantity;
        - duplicate execIds are ignored idempotently.

        A cumulative quantity smaller than the requested quantity
        advances the order to PARTIALLY_FILLED.

        A cumulative quantity equal to the requested quantity
        advances the order to FILLED.
        """

        # Capture callback arrival immediately, before validation/processing,
        # so latency telemetry reflects when BTS first received execDetails.
        callback_received_at_utc = (
            datetime.now(timezone.utc).isoformat()
        )

        ib_execution_time = (
            self._normalize_optional_execution_time(
                getattr(execution, "time", None)
            )
        )

        if not isinstance(
            contract,
            Contract,
        ):
            raise TypeError(
                "'contract' must be an IB Contract."
            )

        if not isinstance(
            execution,
            Execution,
        ):
            raise TypeError(
                "'execution' must be an IB Execution."
            )

        broker_order_id = (
            self._validate_broker_order_id(
                execution.orderId
            )
        )

        exec_id = self._validate_identifier(
            execution.execId,
            "exec_id",
        )

        shares = self._validate_quantity(
            execution.shares,
            "shares",
            allow_zero=False,
        )

        cumulative_quantity = (
            self._validate_quantity(
                execution.cumQty,
                "cum_qty",
                allow_zero=False,
            )
        )

        if shares > cumulative_quantity:
            raise ValueError(
                "IB execution shares cannot exceed "
                "cumulative quantity."
            )

        record = (
            self._find_record_by_broker_order_id(
                broker_order_id
            )
        )

        if record is None:
            raise KeyError(
                f"No BTS execution record exists for "
                f"broker order ID {broker_order_id}."
            )

        self._validate_contract(
            contract,
            record,
        )

        self._validate_execution_side(
            execution.side,
            record,
        )

        requested_quantity = Decimal(
            record.quantity
        )

        if cumulative_quantity > requested_quantity:
            raise RuntimeError(
                f"IB cumulative execution quantity "
                f"{cumulative_quantity} exceeds BTS "
                f"requested quantity {requested_quantity} "
                f"for event {record.event_id!r}."
            )

        if self._execution_id_exists(
            exec_id
        ):
            return IBExecutionDetailsResult(
                outcome=(
                    IBExecutionDetailsOutcome.DUPLICATE
                ),
                broker_order_id=broker_order_id,
                exec_id=exec_id,
                cumulative_quantity=cumulative_quantity,
                execution_record=record,
                reason=(
                    f"IB execution {exec_id!r} was "
                    "already processed."
                ),
            )

        if record.status in {
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }:
            raise RuntimeError(
                f"IB reported execution {exec_id!r} for "
                f"event {record.event_id!r} after BTS "
                f"recorded terminal state "
                f"{record.status.value}."
            )

        if (
            record.status
            is ExecutionStatus.FILLED
        ):
            self._record_execution_id(
                exec_id=exec_id,
                event_id=record.event_id,
                broker_order_id=broker_order_id,
                cumulative_quantity=cumulative_quantity,
                ib_execution_time=ib_execution_time,
                callback_received_at_utc=callback_received_at_utc,
            )

            return IBExecutionDetailsResult(
                outcome=(
                    IBExecutionDetailsOutcome.NO_CHANGE
                ),
                broker_order_id=broker_order_id,
                exec_id=exec_id,
                cumulative_quantity=cumulative_quantity,
                execution_record=record,
                reason=(
                    "Execution was recorded, but BTS "
                    "already considered the order filled."
                ),
            )

        if (
            cumulative_quantity
            == requested_quantity
        ):
            updated = (
                self._execution_ledger.mark_filled(
                    record.event_id
                )
            )

        else:
            if (
                record.status
                is ExecutionStatus.PARTIALLY_FILLED
            ):
                updated = record

            else:
                updated = (
                    self._execution_ledger.mark_partially_filled(
                        record.event_id
                    )
                )

        self._record_execution_id(
            exec_id=exec_id,
            event_id=record.event_id,
            broker_order_id=broker_order_id,
            cumulative_quantity=cumulative_quantity,
            ib_execution_time=ib_execution_time,
            callback_received_at_utc=callback_received_at_utc,
        )

        if updated.status is record.status:
            outcome = (
                IBExecutionDetailsOutcome.NO_CHANGE
            )

            reason = (
                "Execution was recorded without changing "
                "the current BTS execution state."
            )

        else:
            outcome = (
                IBExecutionDetailsOutcome.UPDATED
            )

            reason = (
                f"IB execution advanced BTS state to "
                f"{updated.status.value}."
            )

        return IBExecutionDetailsResult(
            outcome=outcome,
            broker_order_id=broker_order_id,
            exec_id=exec_id,
            cumulative_quantity=cumulative_quantity,
            execution_record=updated,
            reason=reason,
        )

    def contains_execution(
        self,
        exec_id: str,
    ) -> bool:
        """Return True when an IB execId is durably recorded."""

        normalized_exec_id = (
            self._validate_identifier(
                exec_id,
                "exec_id",
            )
        )

        return self._execution_id_exists(
            normalized_exec_id
        )

    def processed_execution_count(
        self,
    ) -> int:
        """Return number of durably processed IB executions."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM ib_execution_details
                """
            ).fetchone()

        if row is None:
            return 0

        return int(
            row[0]
        )

    def _find_record_by_broker_order_id(
        self,
        broker_order_id: int,
    ) -> ExecutionRecord | None:
        """Find one durable execution by IB broker order ID."""

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

    @staticmethod
    def _validate_contract(
        contract: Contract,
        record: ExecutionRecord,
    ) -> None:
        """Ensure the IB execution belongs to expected symbol."""

        raw_symbol = getattr(
            contract,
            "symbol",
            None,
        )

        if (
            not isinstance(raw_symbol, str)
            or not raw_symbol.strip()
        ):
            raise ValueError(
                "IB execution contract must contain "
                "a non-empty symbol."
            )

        symbol = raw_symbol.strip().upper()

        if symbol != record.symbol.upper():
            raise RuntimeError(
                f"IB execution contract symbol "
                f"{symbol!r} does not match BTS symbol "
                f"{record.symbol!r}."
            )

    @staticmethod
    def _validate_execution_side(
        side: Any,
        record: ExecutionRecord,
    ) -> None:
        """Ensure IB execution direction matches BTS intent."""

        if (
            not isinstance(side, str)
            or not side.strip()
        ):
            raise ValueError(
                "IB execution side must be "
                "a non-empty string."
            )

        normalized_side = (
            side.strip().upper()
        )

        if record.intent in {
            "BUY_TO_OPEN",
            "BUY_TO_CLOSE",
        }:
            expected_side = "BOT"

        elif record.intent in {
            "SELL_TO_OPEN",
            "SELL_TO_CLOSE",
        }:
            expected_side = "SLD"

        else:
            raise RuntimeError(
                f"Unsupported BTS trade intent "
                f"{record.intent!r}."
            )

        if normalized_side != expected_side:
            raise RuntimeError(
                f"IB execution side "
                f"{normalized_side!r} does not match "
                f"BTS intent {record.intent!r}."
            )

    def _initialize_execution_table(
        self,
    ) -> None:
        """Create durable IB execId tracking table."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ib_execution_details (
                    exec_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    broker_order_id INTEGER NOT NULL,
                    cumulative_quantity TEXT NOT NULL,
                    ib_execution_time TEXT,
                    callback_received_at_utc TEXT,
                    FOREIGN KEY(event_id)
                        REFERENCES execution_records(event_id)
                )
                """
            )

            existing_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(ib_execution_details)"
                ).fetchall()
            }

            if "ib_execution_time" not in existing_columns:
                connection.execute(
                    """
                    ALTER TABLE ib_execution_details
                    ADD COLUMN ib_execution_time TEXT
                    """
                )

            if "callback_received_at_utc" not in existing_columns:
                connection.execute(
                    """
                    ALTER TABLE ib_execution_details
                    ADD COLUMN callback_received_at_utc TEXT
                    """
                )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_ib_execution_details_event_id
                ON ib_execution_details(event_id)
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_ib_execution_details_order_id
                ON ib_execution_details(broker_order_id)
                """
            )

    def _execution_id_exists(
        self,
        exec_id: str,
    ) -> bool:
        """Return whether an execution ID is already durable."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM ib_execution_details
                WHERE exec_id = ?
                """,
                (
                    exec_id,
                ),
            ).fetchone()

        return row is not None

    def _record_execution_id(
        self,
        *,
        exec_id: str,
        event_id: str,
        broker_order_id: int,
        cumulative_quantity: Decimal,
        ib_execution_time: str | None,
        callback_received_at_utc: str,
    ) -> None:
        """Durably record one processed IB execution."""

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO ib_execution_details (
                        exec_id,
                        event_id,
                        broker_order_id,
                        cumulative_quantity,
                        ib_execution_time,
                        callback_received_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exec_id,
                        event_id,
                        broker_order_id,
                        str(
                            cumulative_quantity
                        ),
                        ib_execution_time,
                        callback_received_at_utc,
                    ),
                )

        except sqlite3.IntegrityError:
            # A concurrent duplicate callback may have inserted
            # the same execId after our initial existence check.
            return

    @staticmethod
    def _normalize_optional_execution_time(
        value: Any,
    ) -> str | None:
        """Normalize IB's optional execution timestamp for telemetry.

        Missing or blank values remain NULL so telemetry can never block
        otherwise-valid broker execution processing.
        """

        if value is None:
            return None

        if not isinstance(value, str):
            return str(value)

        normalized = value.strip()

        return normalized or None

    def _connect(
        self,
    ) -> sqlite3.Connection:
        """Open the execution ledger SQLite database."""

        connection = sqlite3.connect(
            self._execution_ledger.database_path
        )

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        return connection

    @staticmethod
    def _validate_broker_order_id(
        value: Any,
    ) -> int:
        """Validate IB API order ID."""

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
    def _validate_identifier(
        value: Any,
        field_name: str,
    ) -> str:
        """Validate required text identifier."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                f"'{field_name}' must be a "
                "non-empty string."
            )

        return value.strip()

    @staticmethod
    def _validate_quantity(
        value: Any,
        field_name: str,
        *,
        allow_zero: bool,
    ) -> Decimal:
        """Validate execution quantity."""

        if isinstance(
            value,
            bool,
        ):
            raise ValueError(
                f"'{field_name}' must be a "
                "finite positive number."
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
                "finite positive number."
            ) from error

        # Decimal("NaN") and Decimal("Infinity") are valid Decimal
        # objects, but they are not valid execution quantities.
        # Check this before performing numeric comparisons.
        if not normalized.is_finite():
            raise ValueError(
                f"'{field_name}' must be a "
                "finite positive number."
            )

        if allow_zero:
            valid = (
                normalized >= 0
            )

        else:
            valid = (
                normalized > 0
            )

        if not valid:
            raise ValueError(
                f"'{field_name}' must be a "
                "finite positive number."
            )

        return normalized