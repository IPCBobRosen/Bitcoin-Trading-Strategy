"""Tests for the SequenceManager."""

import pytest

from app.sequence_manager import SequenceManager


def test_last_seq_starts_empty() -> None:
    """A new manager should not have a durable sequence yet."""

    manager = SequenceManager()

    assert manager.last_seq is None


def test_first_sequence_is_newer() -> None:
    """The first valid sequence number should be accepted."""

    manager = SequenceManager()

    assert manager.is_newer(100) is True


def test_mark_processed_records_sequence() -> None:
    """A processed sequence should become the durable cursor."""

    manager = SequenceManager()

    manager.mark_processed(100)

    assert manager.last_seq == 100


def test_higher_sequence_is_accepted() -> None:
    """A sequence higher than the durable cursor should be accepted."""

    manager = SequenceManager()

    manager.mark_processed(100)

    assert manager.check_and_mark(105) is True
    assert manager.last_seq == 105


def test_sequence_gap_is_allowed() -> None:
    """Sequence numbers do not need to increase by exactly one."""

    manager = SequenceManager()

    manager.mark_processed(100)

    result = manager.check_and_mark(150)

    assert result is True
    assert manager.last_seq == 150


def test_same_sequence_is_not_newer() -> None:
    """The current durable sequence should not be accepted again."""

    manager = SequenceManager()

    manager.mark_processed(100)

    assert manager.check_and_mark(100) is False
    assert manager.last_seq == 100


def test_older_sequence_is_not_newer() -> None:
    """An older replayed sequence should not move the cursor backward."""

    manager = SequenceManager()

    manager.mark_processed(100)

    assert manager.check_and_mark(90) is False
    assert manager.last_seq == 100


def test_negative_sequence_is_rejected() -> None:
    """Negative Eagle sequence numbers are invalid."""

    manager = SequenceManager()

    with pytest.raises(
        ValueError,
        match="'seq' must be a non-negative integer",
    ):
        manager.check_and_mark(-1)


def test_boolean_sequence_is_rejected() -> None:
    """Boolean values must not be accepted as integer sequence numbers."""

    manager = SequenceManager()

    with pytest.raises(
        ValueError,
        match="'seq' must be a non-negative integer",
    ):
        manager.check_and_mark(True)