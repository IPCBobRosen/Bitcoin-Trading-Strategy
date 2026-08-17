"""Durable SQLite execution and idempotency ledger for BTS."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import sqlite3
from typing import Any

from app.communications.trade_request import TradeRequest


class ExecutionStatus(Enum):
    """Durable lifecycle state for one execution attempt."""

    RESERVED = "Reserved"
    SUBMITTED = "Submitted"
    ACKNOWLEDGED = "Acknowledged"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"


TERMINAL_STATUSES = {
    ExecutionStatus.FILLED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.REJECTED,
}


_ALLOWED_TRANSITIONS: dict[
    ExecutionStatus,
    set[ExecutionStatus],
] = {
    ExecutionStatus.RESERVED: {
        ExecutionStatus.SUBMITTED,
        ExecutionStatus.REJECTED,
    },
    ExecutionStatus.SUBMITTED: {
        ExecutionStatus.ACKNOWLEDGED,
        ExecutionStatus.PARTIALLY_FILLED,
        ExecutionStatus.FILLED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.REJECTED,
    },
    ExecutionStatus.ACKNOWLEDGED: {
        ExecutionStatus.PARTIALLY_FILLED,
        ExecutionStatus.FILLED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.REJECTED,
    },
    ExecutionStatus.PARTIALLY_FILLED: {
        ExecutionStatus.PARTIALLY_FILLED,
        ExecutionStatus.FILLED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.REJECTED,
    },
    ExecutionStatus.FILLED: set(),
    ExecutionStatus.CANCELLED: set(),
    ExecutionStatus.REJECTED: set(),
}


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """Current durable state of one Eagle execution event."""

    event_id: str
    signal_id: str
    symbol: str
    intent: str
    quantity: int
    status: ExecutionStatus
    broker_order_id: int | None
    reason: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def terminal(self) -> bool:
        """Return True when no further execution transitions are allowed."""

        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class ExecutionTransition:
    """One immutable execution-state transition."""

    transition_id: int
    event_id: str
    from_status: ExecutionStatus | None
    to_status: ExecutionStatus
    broker_order_id: int | None
    reason: str | None
    timestamp: datetime


class ExecutionLedger:
    """Persist BTS execution state and idempotency information."""

    def __init__(
        self,
        database_path: str | Path,
    ) -> None:
        """Create or open an execution ledger."""

        if isinstance(
            database_path,
            Path,
        ):
            path = database_path

        elif isinstance(
            database_path,
            str,
        ) and database_path.strip():
            path = Path(
                database_path.strip()
            )

        else:
            raise ValueError(
                "'database_path' must be a non-empty path."
            )

        self._database_path = path

        parent = path.parent

        if str(parent) not in {
            "",
            ".",
        }:
            parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._initialize_database()

    @property
    def database_path(self) -> Path:
        """Return the SQLite database path."""

        return self._database_path

    def reserve(
        self,
        trade_request: TradeRequest,
    ) -> ExecutionRecord:
        """Durably reserve an Eagle event before broker submission.

        Raises:
            ValueError:
                If event_id already exists in the ledger.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        event_id = self._validate_identifier(
            trade_request.event_id,
            "event_id",
        )

        signal_id = self._validate_identifier(
            trade_request.signal_id,
            "signal_id",
        )

        symbol = self._validate_identifier(
            trade_request.symbol,
            "symbol",
        ).upper()

        if (
            not isinstance(
                trade_request.quantity,
                int,
            )
            or isinstance(
                trade_request.quantity,
                bool,
            )
            or trade_request.quantity <= 0
        ):
            raise ValueError(
                "TradeRequest quantity must be a positive integer."
            )

        now = self._utc_now()

        intent = trade_request.intent.value

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO execution_records (
                        event_id,
                        signal_id,
                        symbol,
                        intent,
                        quantity,
                        status,
                        broker_order_id,
                        reason,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        event_id,
                        signal_id,
                        symbol,
                        intent,
                        trade_request.quantity,
                        ExecutionStatus.RESERVED.value,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO execution_transitions (
                        event_id,
                        from_status,
                        to_status,
                        broker_order_id,
                        reason,
                        timestamp
                    )
                    VALUES (?, NULL, ?, NULL, NULL, ?)
                    """,
                    (
                        event_id,
                        ExecutionStatus.RESERVED.value,
                        now.isoformat(),
                    ),
                )

        except sqlite3.IntegrityError as error:
            raise ValueError(
                f"Eagle event {event_id!r} already exists "
                "in the execution ledger."
            ) from error

        record = self.get(
            event_id
        )

        if record is None:
            raise RuntimeError(
                "Execution reservation was not persisted."
            )

        return record

    def transition(
        self,
        event_id: str,
        new_status: ExecutionStatus,
        *,
        broker_order_id: int | None = None,
        reason: str | None = None,
    ) -> ExecutionRecord:
        """Transition one execution record to a new durable state."""

        normalized_event_id = (
            self._validate_identifier(
                event_id,
                "event_id",
            )
        )

        if not isinstance(
            new_status,
            ExecutionStatus,
        ):
            raise TypeError(
                "'new_status' must be an ExecutionStatus."
            )

        normalized_broker_order_id = (
            self._validate_broker_order_id(
                broker_order_id
            )
        )

        normalized_reason = (
            self._normalize_reason(
                reason
            )
        )

        current = self.get(
            normalized_event_id
        )

        if current is None:
            raise KeyError(
                f"Execution event "
                f"{normalized_event_id!r} does not exist."
            )

        allowed = _ALLOWED_TRANSITIONS[
            current.status
        ]

        if new_status not in allowed:
            raise ValueError(
                f"Invalid execution transition "
                f"{current.status.value} -> "
                f"{new_status.value}."
            )

        effective_broker_order_id = (
            normalized_broker_order_id
            if normalized_broker_order_id is not None
            else current.broker_order_id
        )

        now = self._utc_now()

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE execution_records
                SET
                    status = ?,
                    broker_order_id = ?,
                    reason = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    new_status.value,
                    effective_broker_order_id,
                    normalized_reason,
                    now.isoformat(),
                    normalized_event_id,
                ),
            )

            connection.execute(
                """
                INSERT INTO execution_transitions (
                    event_id,
                    from_status,
                    to_status,
                    broker_order_id,
                    reason,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_event_id,
                    current.status.value,
                    new_status.value,
                    effective_broker_order_id,
                    normalized_reason,
                    now.isoformat(),
                ),
            )

        updated = self.get(
            normalized_event_id
        )

        if updated is None:
            raise RuntimeError(
                "Execution transition was not persisted."
            )

        return updated

    def mark_submitted(
        self,
        event_id: str,
        *,
        broker_order_id: int,
    ) -> ExecutionRecord:
        """Mark a reserved event as submitted to the broker."""

        return self.transition(
            event_id,
            ExecutionStatus.SUBMITTED,
            broker_order_id=broker_order_id,
        )

    def mark_acknowledged(
        self,
        event_id: str,
    ) -> ExecutionRecord:
        """Mark a submitted order as broker acknowledged."""

        return self.transition(
            event_id,
            ExecutionStatus.ACKNOWLEDGED,
        )

    def mark_partially_filled(
        self,
        event_id: str,
    ) -> ExecutionRecord:
        """Mark an order as partially filled."""

        return self.transition(
            event_id,
            ExecutionStatus.PARTIALLY_FILLED,
        )

    def mark_filled(
        self,
        event_id: str,
    ) -> ExecutionRecord:
        """Mark an order as completely filled."""

        return self.transition(
            event_id,
            ExecutionStatus.FILLED,
        )

    def mark_cancelled(
        self,
        event_id: str,
        *,
        reason: str | None = None,
    ) -> ExecutionRecord:
        """Mark an order as cancelled."""

        return self.transition(
            event_id,
            ExecutionStatus.CANCELLED,
            reason=reason,
        )

    def mark_rejected(
        self,
        event_id: str,
        *,
        reason: str,
    ) -> ExecutionRecord:
        """Mark an execution attempt as rejected."""

        normalized_reason = (
            self._normalize_reason(
                reason
            )
        )

        if normalized_reason is None:
            raise ValueError(
                "'reason' must be a non-empty string."
            )

        return self.transition(
            event_id,
            ExecutionStatus.REJECTED,
            reason=normalized_reason,
        )

    def get(
        self,
        event_id: str,
    ) -> ExecutionRecord | None:
        """Return one current execution record."""

        normalized_event_id = (
            self._validate_identifier(
                event_id,
                "event_id",
            )
        )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    event_id,
                    signal_id,
                    symbol,
                    intent,
                    quantity,
                    status,
                    broker_order_id,
                    reason,
                    created_at,
                    updated_at
                FROM execution_records
                WHERE event_id = ?
                """,
                (
                    normalized_event_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return self._record_from_row(
            row
        )

    def contains(
        self,
        event_id: str,
    ) -> bool:
        """Return True when the event is durably known."""

        return self.get(
            event_id
        ) is not None

    def history(
        self,
        event_id: str,
    ) -> tuple[ExecutionTransition, ...]:
        """Return complete ordered transition history for an event."""

        normalized_event_id = (
            self._validate_identifier(
                event_id,
                "event_id",
            )
        )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    transition_id,
                    event_id,
                    from_status,
                    to_status,
                    broker_order_id,
                    reason,
                    timestamp
                FROM execution_transitions
                WHERE event_id = ?
                ORDER BY transition_id ASC
                """,
                (
                    normalized_event_id,
                ),
            ).fetchall()

        return tuple(
            self._transition_from_row(
                row
            )
            for row in rows
        )

    def all_records(
        self,
    ) -> tuple[ExecutionRecord, ...]:
        """Return all current execution records."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    signal_id,
                    symbol,
                    intent,
                    quantity,
                    status,
                    broker_order_id,
                    reason,
                    created_at,
                    updated_at
                FROM execution_records
                ORDER BY created_at ASC, event_id ASC
                """
            ).fetchall()

        return tuple(
            self._record_from_row(
                row
            )
            for row in rows
        )

    def _initialize_database(
        self,
    ) -> None:
        """Create execution ledger tables if necessary."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_records (
                    event_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    broker_order_id INTEGER,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    broker_order_id INTEGER,
                    reason TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(event_id)
                        REFERENCES execution_records(event_id)
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_execution_transitions_event_id
                ON execution_transitions(event_id)
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_execution_records_broker_order_id
                ON execution_records(broker_order_id)
                """
            )

    @contextmanager
    def _connect(
        self,
    ) -> Iterator[sqlite3.Connection]:
        """Open, commit or roll back, and always close SQLite."""

        connection = sqlite3.connect(
            self._database_path
        )

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        try:
            with connection:
                yield connection

        finally:
            connection.close()

    @staticmethod
    def _record_from_row(
        row: tuple[Any, ...],
    ) -> ExecutionRecord:
        """Convert a SQLite row to an ExecutionRecord."""

        return ExecutionRecord(
            event_id=row[0],
            signal_id=row[1],
            symbol=row[2],
            intent=row[3],
            quantity=row[4],
            status=ExecutionStatus(
                row[5]
            ),
            broker_order_id=row[6],
            reason=row[7],
            created_at=datetime.fromisoformat(
                row[8]
            ),
            updated_at=datetime.fromisoformat(
                row[9]
            ),
        )

    @staticmethod
    def _transition_from_row(
        row: tuple[Any, ...],
    ) -> ExecutionTransition:
        """Convert a SQLite row to an ExecutionTransition."""

        from_status = (
            ExecutionStatus(
                row[2]
            )
            if row[2] is not None
            else None
        )

        return ExecutionTransition(
            transition_id=row[0],
            event_id=row[1],
            from_status=from_status,
            to_status=ExecutionStatus(
                row[3]
            ),
            broker_order_id=row[4],
            reason=row[5],
            timestamp=datetime.fromisoformat(
                row[6]
            ),
        )

    @staticmethod
    def _validate_identifier(
        value: Any,
        field_name: str,
    ) -> str:
        """Validate a non-empty identifier."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                f"'{field_name}' must be a non-empty string."
            )

        return value.strip()

    @staticmethod
    def _validate_broker_order_id(
        value: Any,
    ) -> int | None:
        """Validate an optional broker order ID."""

        if value is None:
            return None

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(
                "'broker_order_id' must be a "
                "non-negative integer or None."
            )

        return value

    @staticmethod
    def _normalize_reason(
        reason: Any,
    ) -> str | None:
        """Normalize an optional audit reason."""

        if reason is None:
            return None

        if (
            not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError(
                "'reason' must be a non-empty string or None."
            )

        return reason.strip()

    @staticmethod
    def _utc_now() -> datetime:
        """Return current timezone-aware UTC time."""

        return datetime.now(
            timezone.utc
        )