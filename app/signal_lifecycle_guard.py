"""Durably enforce valid Eagle signal lifecycle transitions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.communications.protocol import TradeIntent
from app.communications.trade_request import TradeRequest


class SignalLifecycleState(str, Enum):
    """Durable state of one Eagle signal lifecycle."""

    LONG_OPEN = "LongOpen"
    SHORT_OPEN = "ShortOpen"
    CLOSED = "Closed"


class SignalLifecycleStatus(str, Enum):
    """Outcome of attempting one signal lifecycle transition."""

    ACCEPTED = "Accepted"
    INVALID_TRANSITION = "InvalidTransition"


@dataclass(frozen=True, slots=True)
class SignalLifecycleDecision:
    """Immutable result of one lifecycle transition attempt."""

    status: SignalLifecycleStatus
    signal_id: str
    event_id: str
    intent: TradeIntent
    previous_state: SignalLifecycleState | None
    next_state: SignalLifecycleState | None
    reason: str

    @property
    def allowed(self) -> bool:
        """Return True when the transition was accepted."""

        return (
            self.status
            is SignalLifecycleStatus.ACCEPTED
        )


@dataclass(frozen=True, slots=True)
class SignalLifecycleSnapshot:
    """Immutable durable signal-state snapshot."""

    signal_id: str
    state: SignalLifecycleState
    last_event_id: str


class SignalLifecycleGuard:
    """Persist and enforce valid Eagle signal transitions.

    A signal_id represents one complete Eagle trade lifecycle.

    Valid transitions:

        NEW + BUY_TO_OPEN
            -> LONG_OPEN

        NEW + SELL_TO_OPEN
            -> SHORT_OPEN

        LONG_OPEN + SELL_TO_CLOSE
            -> CLOSED

        SHORT_OPEN + BUY_TO_CLOSE
            -> CLOSED

    Every other transition is rejected without changing
    durable state.

    Closed signal IDs may not be reused for another position.
    """

    def __init__(
        self,
        database_path: str | Path,
    ) -> None:
        """Create or open the durable lifecycle database."""

        self._database_path = Path(
            database_path
        )

        if not str(
            self._database_path
        ).strip():
            raise ValueError(
                "'database_path' must be a valid path."
            )

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()

    @property
    def database_path(self) -> Path:
        """Return the SQLite lifecycle database path."""

        return self._database_path

    def process(
        self,
        trade_request: TradeRequest,
    ) -> SignalLifecycleDecision:
        """Atomically validate and persist one trade transition.

        Rejected transitions leave the existing signal state
        completely unchanged.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        signal_id = self._validate_identifier(
            trade_request.signal_id,
            "signal_id",
        )

        event_id = self._validate_identifier(
            trade_request.event_id,
            "event_id",
        )

        intent = trade_request.intent

        if not isinstance(
            intent,
            TradeIntent,
        ):
            raise TypeError(
                "'trade_request.intent' must be a TradeIntent."
            )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state, last_event_id
                FROM signal_lifecycle
                WHERE signal_id = ?
                """,
                (
                    signal_id,
                ),
            ).fetchone()

            previous_state: (
                SignalLifecycleState | None
            )

            if row is None:
                previous_state = None
            else:
                try:
                    previous_state = (
                        SignalLifecycleState(
                            row[0]
                        )
                    )
                except ValueError as error:
                    raise RuntimeError(
                        "Stored signal lifecycle state "
                        f"is invalid: {row[0]!r}."
                    ) from error

            next_state = (
                self._next_state_for(
                    previous_state=previous_state,
                    intent=intent,
                )
            )

            if next_state is None:
                return SignalLifecycleDecision(
                    status=(
                        SignalLifecycleStatus.INVALID_TRANSITION
                    ),
                    signal_id=signal_id,
                    event_id=event_id,
                    intent=intent,
                    previous_state=previous_state,
                    next_state=None,
                    reason=(
                        self._rejection_reason(
                            previous_state=previous_state,
                            intent=intent,
                        )
                    ),
                )

            if previous_state is None:
                connection.execute(
                    """
                    INSERT INTO signal_lifecycle (
                        signal_id,
                        state,
                        last_event_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        signal_id,
                        next_state.value,
                        event_id,
                    ),
                )

            else:
                connection.execute(
                    """
                    UPDATE signal_lifecycle
                    SET state = ?,
                        last_event_id = ?
                    WHERE signal_id = ?
                    """,
                    (
                        next_state.value,
                        event_id,
                        signal_id,
                    ),
                )

            return SignalLifecycleDecision(
                status=(
                    SignalLifecycleStatus.ACCEPTED
                ),
                signal_id=signal_id,
                event_id=event_id,
                intent=intent,
                previous_state=previous_state,
                next_state=next_state,
                reason=(
                    self._acceptance_reason(
                        previous_state=previous_state,
                        next_state=next_state,
                        intent=intent,
                    )
                ),
            )

    def get_state(
        self,
        signal_id: str,
    ) -> SignalLifecycleState | None:
        """Return durable state for one signal ID."""

        normalized_signal_id = (
            self._validate_identifier(
                signal_id,
                "signal_id",
            )
        )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state
                FROM signal_lifecycle
                WHERE signal_id = ?
                """,
                (
                    normalized_signal_id,
                ),
            ).fetchone()

        if row is None:
            return None

        try:
            return SignalLifecycleState(
                row[0]
            )

        except ValueError as error:
            raise RuntimeError(
                "Stored signal lifecycle state "
                f"is invalid: {row[0]!r}."
            ) from error

    def get_snapshot(
        self,
        signal_id: str,
    ) -> SignalLifecycleSnapshot | None:
        """Return durable lifecycle snapshot for one signal."""

        normalized_signal_id = (
            self._validate_identifier(
                signal_id,
                "signal_id",
            )
        )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state, last_event_id
                FROM signal_lifecycle
                WHERE signal_id = ?
                """,
                (
                    normalized_signal_id,
                ),
            ).fetchone()

        if row is None:
            return None

        try:
            state = SignalLifecycleState(
                row[0]
            )

        except ValueError as error:
            raise RuntimeError(
                "Stored signal lifecycle state "
                f"is invalid: {row[0]!r}."
            ) from error

        return SignalLifecycleSnapshot(
            signal_id=normalized_signal_id,
            state=state,
            last_event_id=str(
                row[1]
            ),
        )

    def all_snapshots(
        self,
    ) -> tuple[
        SignalLifecycleSnapshot,
        ...,
    ]:
        """Return every durable lifecycle snapshot."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    signal_id,
                    state,
                    last_event_id
                FROM signal_lifecycle
                ORDER BY signal_id
                """
            ).fetchall()

        snapshots: list[
            SignalLifecycleSnapshot
        ] = []

        for row in rows:
            try:
                state = SignalLifecycleState(
                    row[1]
                )

            except ValueError as error:
                raise RuntimeError(
                    "Stored signal lifecycle state "
                    f"is invalid: {row[1]!r}."
                ) from error

            snapshots.append(
                SignalLifecycleSnapshot(
                    signal_id=str(
                        row[0]
                    ),
                    state=state,
                    last_event_id=str(
                        row[2]
                    ),
                )
            )

        return tuple(
            snapshots
        )

    @staticmethod
    def _next_state_for(
        *,
        previous_state: SignalLifecycleState | None,
        intent: TradeIntent,
    ) -> SignalLifecycleState | None:
        """Return valid next state or None when rejected."""

        if previous_state is None:
            if (
                intent
                is TradeIntent.BUY_TO_OPEN
            ):
                return (
                    SignalLifecycleState.LONG_OPEN
                )

            if (
                intent
                is TradeIntent.SELL_TO_OPEN
            ):
                return (
                    SignalLifecycleState.SHORT_OPEN
                )

            return None

        if (
            previous_state
            is SignalLifecycleState.LONG_OPEN
        ):
            if (
                intent
                is TradeIntent.SELL_TO_CLOSE
            ):
                return (
                    SignalLifecycleState.CLOSED
                )

            return None

        if (
            previous_state
            is SignalLifecycleState.SHORT_OPEN
        ):
            if (
                intent
                is TradeIntent.BUY_TO_CLOSE
            ):
                return (
                    SignalLifecycleState.CLOSED
                )

            return None

        if (
            previous_state
            is SignalLifecycleState.CLOSED
        ):
            return None

        raise RuntimeError(
            "Unsupported signal lifecycle state: "
            f"{previous_state!r}."
        )

    @staticmethod
    def _acceptance_reason(
        *,
        previous_state: SignalLifecycleState | None,
        next_state: SignalLifecycleState,
        intent: TradeIntent,
    ) -> str:
        """Build audit reason for accepted transition."""

        previous_text = (
            "NEW"
            if previous_state is None
            else previous_state.value
        )

        return (
            f"Signal lifecycle transition accepted: "
            f"{previous_text} + {intent.value} "
            f"-> {next_state.value}."
        )

    @staticmethod
    def _rejection_reason(
        *,
        previous_state: SignalLifecycleState | None,
        intent: TradeIntent,
    ) -> str:
        """Build audit reason for rejected transition."""

        previous_text = (
            "NEW"
            if previous_state is None
            else previous_state.value
        )

        return (
            "Signal lifecycle transition rejected: "
            f"{previous_text} + {intent.value}."
        )

    def _initialize_database(
        self,
    ) -> None:
        """Create durable lifecycle table."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_lifecycle (
                    signal_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    last_event_id TEXT NOT NULL
                )
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

        try:
            with connection:
                yield connection

        finally:
            connection.close()

    @staticmethod
    def _validate_identifier(
        value: str,
        field_name: str,
    ) -> str:
        """Validate and normalize Eagle identifier."""

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                f"'{field_name}' must be a non-empty string."
            )

        return value.strip()