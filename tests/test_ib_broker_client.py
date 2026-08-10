"""Tests for the Interactive Brokers position client."""

from decimal import Decimal

import pytest

from app.broker_position_adapter import RawBrokerPosition
from app.ib_broker_client import (
    IBBrokerClient,
    IBPositionRecord,
)


def test_new_client_has_no_completed_snapshot() -> None:
    """New IB clients should not expose an uncollected snapshot."""

    client = IBBrokerClient()

    assert client.snapshot_in_progress is False
    assert client.snapshot_complete is False


def test_begin_snapshot_sets_collection_state() -> None:
    """Beginning reqPositions collection should reset snapshot state."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    assert client.snapshot_in_progress is True
    assert client.snapshot_complete is False


def test_position_received_before_snapshot_start_is_rejected() -> None:
    """Position callbacks must belong to an active snapshot."""

    client = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="has not been started",
    ):
        client.receive_position(
            IBPositionRecord(
                account="DU123456",
                symbol="MBT",
                position=1,
            )
        )


def test_receive_long_position() -> None:
    """Positive IB quantities should be preserved for the adapter."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=Decimal("2"),
        )
    )

    client.finish_position_snapshot()

    assert client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBT",
            quantity=2,
            signal_id=None,
        ),
    )


def test_receive_short_position() -> None:
    """Negative IB quantities should be preserved for normalization."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=Decimal("-3"),
        )
    )

    client.finish_position_snapshot()

    assert client.get_raw_positions() == (
        RawBrokerPosition(
            symbol="MBT",
            quantity=-3,
            signal_id=None,
        ),
    )


def test_flat_position_is_ignored() -> None:
    """Zero IB positions should not enter reconciliation."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=0,
        )
    )

    client.finish_position_snapshot()

    assert client.get_raw_positions() == ()


def test_local_symbol_is_preferred() -> None:
    """IB localSymbol should be preferred when available."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            local_symbol="MBTQ6",
            position=1,
        )
    )

    client.finish_position_snapshot()

    assert client.get_raw_positions()[0].symbol == "MBTQ6"


def test_symbol_is_used_when_local_symbol_missing() -> None:
    """Root symbol should be used when localSymbol is unavailable."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=1,
        )
    )

    client.finish_position_snapshot()

    assert client.get_raw_positions()[0].symbol == "MBT"


def test_missing_symbol_is_rejected() -> None:
    """A position requires a usable IB contract identifier."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    with pytest.raises(
        ValueError,
        match="symbol or local_symbol",
    ):
        client.receive_position(
            IBPositionRecord(
                account="DU123456",
                symbol="",
                position=1,
            )
        )


def test_fractional_futures_quantity_is_rejected() -> None:
    """Futures contract quantities must normalize to whole contracts."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    with pytest.raises(
        ValueError,
        match="whole number",
    ):
        client.receive_position(
            IBPositionRecord(
                account="DU123456",
                symbol="MBT",
                position=Decimal("1.5"),
            )
        )


def test_boolean_quantity_is_rejected() -> None:
    """Boolean values must not be interpreted as position quantities."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    with pytest.raises(
        ValueError,
        match="numeric",
    ):
        client.receive_position(
            IBPositionRecord(
                account="DU123456",
                symbol="MBT",
                position=True,
            )
        )


def test_finish_without_start_is_rejected() -> None:
    """positionEnd cannot complete a snapshot that was never started."""

    client = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="has not been started",
    ):
        client.finish_position_snapshot()


def test_get_positions_before_completion_is_rejected() -> None:
    """Incomplete IB snapshots must never be used for reconciliation."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    with pytest.raises(
        RuntimeError,
        match="not complete",
    ):
        client.get_raw_positions()


def test_finish_snapshot_marks_complete() -> None:
    """IB positionEnd should mark the initial snapshot complete."""

    client = IBBrokerClient()

    client.begin_position_snapshot()
    client.finish_position_snapshot()

    assert client.snapshot_in_progress is False
    assert client.snapshot_complete is True


def test_new_snapshot_replaces_previous_positions() -> None:
    """Each reqPositions snapshot should replace prior collected state."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=2,
        )
    )

    client.finish_position_snapshot()

    assert len(
        client.get_raw_positions()
    ) == 1

    client.begin_position_snapshot()
    client.finish_position_snapshot()

    assert client.get_raw_positions() == ()


def test_invalid_position_object_is_rejected() -> None:
    """IB callbacks must be represented by IBPositionRecord."""

    client = IBBrokerClient()

    client.begin_position_snapshot()

    with pytest.raises(
        TypeError,
        match="'position' must be an IBPositionRecord",
    ):
        client.receive_position(
            object()  # type: ignore[arg-type]
        )
        