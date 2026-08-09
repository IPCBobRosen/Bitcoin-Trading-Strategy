"""Track Eagle sequence numbers used for ordering and replay recovery."""


class SequenceManager:
    """Track the highest durable Eagle sequence number seen by BTS.

    Eagle sequence numbers are authoritative ordering cursors.

    Gaps are allowed because filtered channels and heartbeat/control events
    may advance the global sequence without every event reaching this
    processing path.
    """

    def __init__(self) -> None:
        """Create a sequence manager with no previously recorded sequence."""

        self._last_seq: int | None = None

    @property
    def last_seq(self) -> int | None:
        """Return the highest sequence number recorded so far."""

        return self._last_seq

    def is_newer(self, seq: int) -> bool:
        """Return True if seq is newer than the current durable cursor."""

        self._validate_seq(seq)

        if self._last_seq is None:
            return True

        return seq > self._last_seq

    def mark_processed(self, seq: int) -> None:
        """Advance the durable sequence cursor when seq is newer."""

        self._validate_seq(seq)

        if self._last_seq is None or seq > self._last_seq:
            self._last_seq = seq

    def check_and_mark(self, seq: int) -> bool:
        """Check whether seq is newer and advance the cursor if it is.

        Returns:
            True:
                seq is newer than the current cursor and is now recorded.

            False:
                seq is equal to or older than the current cursor.
        """

        self._validate_seq(seq)

        if not self.is_newer(seq):
            return False

        self._last_seq = seq
        return True

    @staticmethod
    def _validate_seq(seq: int) -> None:
        """Validate an Eagle sequence number."""

        if (
            not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq < 0
        ):
            raise ValueError("'seq' must be a non-negative integer.")