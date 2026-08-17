"""Tests for the Eagle reconnect reconciliation-mismatch harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.communications.eagle_client import EagleClient
from app.event_store import EventStore
from app.reconciliation_manager import ReconciliationStatus

from scripts.test_eagle_reconnect_reconciliation_mismatch import (
    EXPECTED_DISCONNECT_CURSOR,
    EXPECTED_FINAL_CURSOR,
    HEARTBEAT_TIMEOUT_SECONDS,
    SYNTHETIC_BROKER_POSITION,
    ReconciliationMismatchResult,
    build_client,
)


def create_success_result() -> ReconciliationMismatchResult:
    """Create one representative safe mismatch result."""

    return ReconciliationMismatchResult(
        initial_cursor=None,

        first_connection_uri=(
            "ws://localhost:8766"
        ),

        first_hello_received=True,

        first_replay_expected=3,
        first_replay_processed=2,
        first_replay_complete=False,

        disconnect_detected=True,
        cursor_after_disconnect=3,

        second_connection_uri=(
            "ws://localhost:8766?since_seq=3"
        ),

        second_hello_received=True,
        second_requested_since_seq=3,

        second_replay_expected=1,
        second_replay_processed=1,
        second_replay_complete=True,

        reconciliation_status=(
            ReconciliationStatus.MISMATCHED
        ),

        reconciliation_matched=False,

        heartbeat_healthy=True,
        reconnect_ready=False,

        final_cursor=6,
    )


def script_source() -> str:
    """Return the complete mismatch-harness source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_reconnect_reconciliation_mismatch.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def test_expected_disconnect_cursor() -> None:
    """Interrupted first session should stop at sequence 3."""

    assert EXPECTED_DISCONNECT_CURSOR == 3


def test_expected_final_cursor() -> None:
    """Recovered transport should reach sequence 6."""

    assert EXPECTED_FINAL_CURSOR == 6


def test_heartbeat_timeout() -> None:
    """Harness should use established Eagle heartbeat timeout."""

    assert HEARTBEAT_TIMEOUT_SECONDS == 45


def test_synthetic_broker_position_is_non_flat() -> None:
    """Synthetic snapshot must deliberately disagree with Eagle flat."""

    assert SYNTHETIC_BROKER_POSITION == {
        "symbol": "MBT",
        "quantity": 1,
    }

    assert (
        SYNTHETIC_BROKER_POSITION["quantity"]
        != 0
    )


def test_result_is_immutable() -> None:
    """Mismatch result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_cursor = 99  # type: ignore[misc]


def test_safe_mismatch_result_is_successful() -> None:
    """Expected fail-closed mismatch scenario should pass."""

    result = create_success_result()

    assert result.successful is True


def test_success_requires_real_disconnect() -> None:
    """Scenario cannot pass without detecting the socket failure."""

    result = create_success_result()

    assert result.disconnect_detected is True
    assert result.cursor_after_disconnect == 3


def test_first_replay_must_be_incomplete() -> None:
    """First session must terminate before replay is drained."""

    result = create_success_result()

    assert result.first_replay_complete is False

    assert (
        result.first_replay_processed
        < result.first_replay_expected
    )


def test_second_replay_must_complete() -> None:
    """Recovery session must drain the remaining replay."""

    result = create_success_result()

    assert result.second_replay_complete is True

    assert (
        result.second_replay_processed
        == result.second_replay_expected
    )


def test_result_requires_mismatched_reconciliation() -> None:
    """Successful negative test requires explicit mismatch."""

    result = create_success_result()

    assert (
        result.reconciliation_status
        is ReconciliationStatus.MISMATCHED
    )

    assert result.reconciliation_matched is False


def test_result_requires_healthy_heartbeat() -> None:
    """Transport recovery should otherwise be healthy."""

    result = create_success_result()

    assert result.heartbeat_healthy is True


def test_result_requires_not_ready() -> None:
    """Mismatch must keep reconnect readiness false."""

    result = create_success_result()

    assert result.reconnect_ready is False


def test_build_client_without_cursor(
    tmp_path,
) -> None:
    """Initial client should connect without since_seq."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    client = build_client(
        uri="ws://localhost:8766",
        event_store=store,
    )

    assert isinstance(
        client,
        EagleClient,
    )

    assert client.since_seq is None

    assert (
        client._connection_uri()
        == "ws://localhost:8766"
    )


def test_build_client_uses_disconnect_cursor(
    tmp_path,
) -> None:
    """Recovery client must reconnect from durable sequence 3."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    store.mark_seq_processed(
        3
    )

    client = build_client(
        uri="ws://localhost:8766",
        event_store=store,
    )

    assert client.since_seq == 3

    assert (
        client._connection_uri()
        == "ws://localhost:8766?since_seq=3"
    )


def test_build_client_reads_latest_cursor(
    tmp_path,
) -> None:
    """Each newly created client must read current storage."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    first_client = build_client(
        uri="ws://localhost:8766",
        event_store=store,
    )

    assert first_client.since_seq is None

    store.mark_seq_processed(
        3
    )

    second_client = build_client(
        uri="ws://localhost:8766",
        event_store=store,
    )

    assert second_client.since_seq == 3


def test_script_handles_connection_error() -> None:
    """Harness must explicitly detect the real disconnect."""

    source = script_source()

    assert (
        "except ConnectionError as error:"
        in source
    )


def test_script_creates_second_client() -> None:
    """Harness must create a fresh recovery client."""

    source = script_source()

    assert (
        "second_client = build_client("
        in source
    )


def test_script_performs_reconciliation() -> None:
    """Recovery session must actually invoke reconciliation."""

    source = script_source()

    assert (
        "reconciliation_manager.reconcile("
        in source
    )


def test_script_supplies_synthetic_position() -> None:
    """Recovery reconciliation must include mismatch snapshot."""

    source = script_source()

    assert (
        "SYNTHETIC_BROKER_POSITION"
        in source
    )


def test_script_evaluates_reconnect_readiness() -> None:
    """Harness must evaluate the production readiness gate."""

    source = script_source()

    assert (
        "ReconnectReadiness("
        in source
    )

    assert (
        "reconnect_readiness.evaluate()"
        in source
    )


def test_script_contains_no_ib_execution_path() -> None:
    """Mismatch harness must contain no IB execution path."""

    source = script_source()

    forbidden_tokens = (
        "ibapi",
        "IBExecutionClient",
        "IBOrderFactory",
        "IBBrokerClient",
        "placeOrder",
        "place_order_function",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_script_imports_no_trade_coordinator() -> None:
    """Harness must not import the trading-decision layer."""

    source = script_source()

    assert (
        "from app.trade_coordinator import"
        not in source
    )

    assert (
        "import app.trade_coordinator"
        not in source
    )


def test_script_imports_no_trade_request() -> None:
    """Harness must not import executable trade requests."""

    source = script_source()

    assert (
        "from app.communications.trade_request import"
        not in source
    )

    assert (
        "import app.communications.trade_request"
        not in source
    )


def test_script_imports_no_resume_manager() -> None:
    """Negative harness must never resume trading."""

    source = script_source()

    assert (
        "from app.resume_manager import"
        not in source
    )

    assert (
        "import app.resume_manager"
        not in source
    )


def test_script_contains_no_broker_client() -> None:
    """Harness must not instantiate any broker connection."""

    source = script_source()

    forbidden_tokens = (
        "FakeBrokerClient",
        "BrokerPositionProvider",
        "IBBrokerClient",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_matching_reconciliation_would_fail_negative_test() -> None:
    """Negative scenario must not pass if reconciliation is matched."""

    result = create_success_result()

    modified = ReconciliationMismatchResult(
        initial_cursor=result.initial_cursor,

        first_connection_uri=(
            result.first_connection_uri
        ),

        first_hello_received=(
            result.first_hello_received
        ),

        first_replay_expected=(
            result.first_replay_expected
        ),

        first_replay_processed=(
            result.first_replay_processed
        ),

        first_replay_complete=(
            result.first_replay_complete
        ),

        disconnect_detected=(
            result.disconnect_detected
        ),

        cursor_after_disconnect=(
            result.cursor_after_disconnect
        ),

        second_connection_uri=(
            result.second_connection_uri
        ),

        second_hello_received=(
            result.second_hello_received
        ),

        second_requested_since_seq=(
            result.second_requested_since_seq
        ),

        second_replay_expected=(
            result.second_replay_expected
        ),

        second_replay_processed=(
            result.second_replay_processed
        ),

        second_replay_complete=(
            result.second_replay_complete
        ),

        reconciliation_status=(
            ReconciliationStatus.MATCHED
        ),

        reconciliation_matched=True,

        heartbeat_healthy=(
            result.heartbeat_healthy
        ),

        reconnect_ready=(
            result.reconnect_ready
        ),

        final_cursor=(
            result.final_cursor
        ),
    )

    assert modified.successful is False


def test_ready_state_would_fail_negative_test() -> None:
    """Negative scenario must fail if readiness becomes true."""

    result = create_success_result()

    modified = ReconciliationMismatchResult(
        initial_cursor=result.initial_cursor,

        first_connection_uri=(
            result.first_connection_uri
        ),

        first_hello_received=(
            result.first_hello_received
        ),

        first_replay_expected=(
            result.first_replay_expected
        ),

        first_replay_processed=(
            result.first_replay_processed
        ),

        first_replay_complete=(
            result.first_replay_complete
        ),

        disconnect_detected=(
            result.disconnect_detected
        ),

        cursor_after_disconnect=(
            result.cursor_after_disconnect
        ),

        second_connection_uri=(
            result.second_connection_uri
        ),

        second_hello_received=(
            result.second_hello_received
        ),

        second_requested_since_seq=(
            result.second_requested_since_seq
        ),

        second_replay_expected=(
            result.second_replay_expected
        ),

        second_replay_processed=(
            result.second_replay_processed
        ),

        second_replay_complete=(
            result.second_replay_complete
        ),

        reconciliation_status=(
            result.reconciliation_status
        ),

        reconciliation_matched=(
            result.reconciliation_matched
        ),

        heartbeat_healthy=(
            result.heartbeat_healthy
        ),

        reconnect_ready=True,

        final_cursor=(
            result.final_cursor
        ),
    )

    assert modified.successful is False


def test_final_cursor_must_reach_six() -> None:
    """Transport recovery must still durably reach sequence 6."""

    result = create_success_result()

    assert result.final_cursor == 6


def test_second_connection_must_request_three() -> None:
    """Recovery connection must resume from interrupted cursor."""

    result = create_success_result()

    assert (
        result.second_connection_uri
        == "ws://localhost:8766?since_seq=3"
    )

    assert (
        result.second_requested_since_seq
        == 3
    )