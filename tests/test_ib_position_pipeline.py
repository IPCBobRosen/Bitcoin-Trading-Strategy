"""Integration tests for the IB position reconciliation pipeline."""

from decimal import Decimal

from app.broker_position_provider import AdapterBrokerPositionProvider
from app.ib_broker_client import (
    IBBrokerClient,
    IBPositionRecord,
)
from app.reconciliation_manager import (
    ReconciliationManager,
    ReconciliationStatus,
)


def test_empty_ib_snapshot_matches_empty_eagle_book() -> None:
    """An empty completed IB snapshot should match an empty Eagle book."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()
    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True
    assert result.broker_positions == ()


def test_ib_long_position_flows_through_pipeline() -> None:
    """A positive IB position should normalize to a LONG broker position."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=Decimal("2"),
            local_symbol="MBTQ6",
        )
    )

    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "symbol": "MBTQ6",
                "side": "LONG",
                "quantity": 2,
            }
        ],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True


def test_ib_short_position_flows_through_pipeline() -> None:
    """A negative IB position should normalize to a SHORT broker position."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=Decimal("-3"),
            local_symbol="MBTQ6",
        )
    )

    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "symbol": "MBTQ6",
                "side": "SHORT",
                "quantity": 3,
            }
        ],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True


def test_ib_and_eagle_quantity_mismatch_is_detected() -> None:
    """Different IB and Eagle quantities must fail reconciliation."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=1,
            local_symbol="MBTQ6",
        )
    )

    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "symbol": "MBTQ6",
                "side": "LONG",
                "quantity": 2,
            }
        ],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MISMATCHED
    assert result.matched is False


def test_ib_and_eagle_side_mismatch_is_detected() -> None:
    """Different IB and Eagle position sides must fail reconciliation."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=-1,
            local_symbol="MBTQ6",
        )
    )

    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "symbol": "MBTQ6",
                "side": "LONG",
                "quantity": 1,
            }
        ],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MISMATCHED
    assert result.matched is False


def test_ib_missing_position_is_detected() -> None:
    """An Eagle position missing from IB must fail reconciliation."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()
    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "symbol": "MBTQ6",
                "side": "LONG",
                "quantity": 1,
            }
        ],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MISMATCHED


def test_extra_ib_position_is_detected() -> None:
    """An unexpected IB position must fail reconciliation."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=1,
            local_symbol="MBTQ6",
        )
    )

    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MISMATCHED


def test_multiple_ib_positions_reconcile_when_books_match() -> None:
    """Multiple IB positions should reconcile when both books agree."""

    ib_client = IBBrokerClient()

    ib_client.begin_position_snapshot()

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MBT",
            position=1,
            local_symbol="MBTQ6",
        )
    )

    ib_client.receive_position(
        IBPositionRecord(
            account="DU123456",
            symbol="MES",
            position=-2,
            local_symbol="MESU6",
        )
    )

    ib_client.finish_position_snapshot()

    provider = AdapterBrokerPositionProvider(
        ib_client.get_raw_positions
    )

    reconciliation_manager = ReconciliationManager()

    result = reconciliation_manager.reconcile(
        eagle_positions=[
            {
                "symbol": "MESU6",
                "side": "SHORT",
                "quantity": 2,
            },
            {
                "symbol": "MBTQ6",
                "side": "LONG",
                "quantity": 1,
            },
        ],
        broker_positions=provider.get_positions(),
    )

    assert result.status is ReconciliationStatus.MATCHED
    assert result.matched is True