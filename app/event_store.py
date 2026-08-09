"""Persistent storage for Eagle event IDs and sequence cursors."""

import sqlite3
from enum import Enum
from pathlib import Path

class EventProcessingResult(Enum):
    """Result of atomically evaluating an Eagle event."""

    ACCEPTED = "Accepted"
    DUPLICATE_EVENT = "DuplicateEvent"
    OUT_OF_SEQUENCE = "OutOfSequence"


class EventStore:
    """Persist Eagle processing state in a SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        """Create or open the SQLite event store.

        Args:
            database_path:
                File path to the SQLite database.
        """

        self._database_path = Path(database_path)

        if not str(self._database_path).strip():
            raise ValueError("'database_path' must be a valid path.")

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()

    @property
    def database_path(self) -> Path:
        """Return the SQLite database path."""

        return self._database_path

    def has_processed_event(self, event_id: str) -> bool:
        """Return True if event_id has already been persisted."""

        self._validate_event_id(event_id)

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM processed_events
                WHERE event_id = ?
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()

        return row is not None

    def mark_event_processed(self, event_id: str) -> None:
        """Persist an event ID as processed.

        Re-marking the same event ID is safe and does not create duplicates.
        """

        self._validate_event_id(event_id)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO processed_events (event_id)
                VALUES (?)
                """,
                (event_id,),
            )

    def check_and_mark_event(self, event_id: str) -> bool:
        """Atomically check whether event_id is new and persist it.

        Returns:
            True:
                The event ID was new and is now persisted.

            False:
                The event ID had already been persisted.
        """

        self._validate_event_id(event_id)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO processed_events (event_id)
                VALUES (?)
                """,
                (event_id,),
            )

            return cursor.rowcount == 1

    def get_last_seq(self) -> int | None:
        """Return the last durable Eagle sequence cursor."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT last_seq
                FROM sequence_state
                WHERE id = 1
                """
            ).fetchone()

        if row is None:
            return None

        return int(row[0])

    def mark_seq_processed(self, seq: int) -> None:
        """Advance the durable sequence cursor when seq is newer."""

        self._validate_seq(seq)

        with self._connect() as connection:
            current_row = connection.execute(
                """
                SELECT last_seq
                FROM sequence_state
                WHERE id = 1
                """
            ).fetchone()

            if current_row is None:
                connection.execute(
                    """
                    INSERT INTO sequence_state (id, last_seq)
                    VALUES (1, ?)
                    """,
                    (seq,),
                )
                return

            current_seq = int(current_row[0])

            if seq > current_seq:
                connection.execute(
                    """
                    UPDATE sequence_state
                    SET last_seq = ?
                    WHERE id = 1
                    """,
                    (seq,),
                )

    def check_and_mark_seq(self, seq: int) -> bool:
        """Check whether seq is newer and persist it if so."""

        self._validate_seq(seq)

        with self._connect() as connection:
            current_row = connection.execute(
                """
                SELECT last_seq
                FROM sequence_state
                WHERE id = 1
                """
            ).fetchone()

            if current_row is None:
                connection.execute(
                    """
                    INSERT INTO sequence_state (id, last_seq)
                    VALUES (1, ?)
                    """,
                    (seq,),
                )
                return True

            current_seq = int(current_row[0])

            if seq <= current_seq:
                return False

            connection.execute(
                """
                UPDATE sequence_state
                SET last_seq = ?
                WHERE id = 1
                """,
                (seq,),
            )

            return True

      
    def check_and_mark_event_with_seq(
        self,
        event_id: str,
        seq: int,
    ) -> EventProcessingResult:
        """Atomically evaluate and persist an Eagle event and sequence.

        The event ID and sequence cursor are committed together.

        Returns:
            EventProcessingResult.ACCEPTED:
                The event ID is new and the sequence is newer. Both are
                persisted in one SQLite transaction.

            EventProcessingResult.DUPLICATE_EVENT:
                The event ID has already been processed. Nothing changes.

            EventProcessingResult.OUT_OF_SEQUENCE:
                The event ID is new, but the sequence is equal to or older
                than the durable cursor. Nothing changes.
        """

        self._validate_event_id(event_id)
        self._validate_seq(seq)

        with self._connect() as connection:
            duplicate_row = connection.execute(
                """
                SELECT 1
                FROM processed_events
                WHERE event_id = ?
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()

            if duplicate_row is not None:
                return EventProcessingResult.DUPLICATE_EVENT

            sequence_row = connection.execute(
                """
                SELECT last_seq
                FROM sequence_state
                WHERE id = 1
                """
            ).fetchone()

            if sequence_row is not None:
                current_seq = int(sequence_row[0])

                if seq <= current_seq:
                    return EventProcessingResult.OUT_OF_SEQUENCE

            connection.execute(
                """
                INSERT INTO processed_events (event_id)
                VALUES (?)
                """,
                (event_id,),
            )

            if sequence_row is None:
                connection.execute(
                    """
                    INSERT INTO sequence_state (id, last_seq)
                    VALUES (1, ?)
                    """,
                    (seq,),
                )
            else:
                connection.execute(
                    """
                    UPDATE sequence_state
                    SET last_seq = ?
                    WHERE id = 1
                    """,
                    (seq,),
                )

        return EventProcessingResult.ACCEPTED        
    def _initialize_database(self) -> None:
        """Create required SQLite tables if they do not already exist."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sequence_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_seq INTEGER NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection."""

        return sqlite3.connect(self._database_path)

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        """Validate an Eagle event ID."""

        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("'event_id' must be a non-empty string.")

    @staticmethod
    def _validate_seq(seq: int) -> None:
        """Validate an Eagle sequence number."""

        if (
            not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq < 0
        ):
            raise ValueError("'seq' must be a non-negative integer.")