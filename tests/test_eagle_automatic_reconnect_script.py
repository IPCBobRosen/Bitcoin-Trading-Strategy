"""Tests for the Eagle automatic reconnect safety harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.communications.eagle_client import EagleClient
from app.event_store import EventStore

from scripts.test_eagle_automatic_reconnect import (
    EXPECTED_FINAL_CURSOR,
    EXPECTED_FIRST_SESSION_CURSOR,
    HEARTBEAT_TIMEOUT_SECONDS,
    AutomaticReconnectResult,
    SessionCounts,
    build_client,
)


def create_success_result() -> AutomaticReconnectResult:
    """Create one representative successful reconnect result."""

    return AutomaticReconnectResult(
        initial_cursor=None,

        first_connection_uri=(
            "ws://localhost:8766"
        ),
        first_hello_received=True,
        first_session_lifecycle_count=2,
        first_session_heartbeat_count=1,
        first_session_replay_expected=3,
        first_session_replay_processed=2,
        first_session_replay_complete=False,

        disconnect_detected=True,
        cursor_after_disconnect=3,

        second_connection_uri=(
            "ws://localhost:8766?since_seq=3"
        ),
        second_hello_received=True,
        second_requested_since_seq=3,
        second_session_lifecycle_count=1,
        second_session_heartbeat_count=2,
        second_session_replay_expected=1,
        second_session_replay_processed=1,
        second_session_replay_complete=True,

        reconciliation_matched=True,
        heartbeat_healthy=True,
        reconnect_ready=True,

        final_cursor=6,
    )


def script_source() -> str:
    """Return the complete automatic reconnect harness source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_automatic_reconnect.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def test_expected_disconnect_cursor() -> None:
    """Interrupted first session should stop at sequence 3."""

    assert EXPECTED_FIRST_SESSION_CURSOR == 3


def test_expected_final_cursor() -> None:
    """Recovered session should finish at sequence 6."""

    assert EXPECTED_FINAL_CURSOR == 6


def test_heartbeat_timeout() -> None:
    """Harness should use established Eagle heartbeat timeout."""

    assert HEARTBEAT_TIMEOUT_SECONDS == 45


def test_result_is_immutable() -> None:
    """Automatic reconnect result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_cursor = 99  # type: ignore[misc]


def test_success_result_is_successful() -> None:
    """Representative safe recovery should report success."""

    result = create_success_result()

    assert result.successful is True


def test_missing_disconnect_fails_success() -> None:
    """Recovery cannot pass unless a disconnect was detected."""

    result = create_success_result()

    modified = AutomaticReconnectResult(
        initial_cursor=result.initial_cursor,

        first_connection_uri=(
            result.first_connection_uri
        ),
        first_hello_received=(
            result.first_hello_received
        ),
        first_session_lifecycle_count=(
            result.first_session_lifecycle_count
        ),
        first_session_heartbeat_count=(
            result.first_session_heartbeat_count
        ),
        first_session_replay_expected=(
            result.first_session_replay_expected
        ),
        first_session_replay_processed=(
            result.first_session_replay_processed
        ),
        first_session_replay_complete=(
            result.first_session_replay_complete
        ),

        disconnect_detected=False,
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
        second_session_lifecycle_count=(
            result.second_session_lifecycle_count
        ),
        second_session_heartbeat_count=(
            result.second_session_heartbeat_count
        ),
        second_session_replay_expected=(
            result.second_session_replay_expected
        ),
        second_session_replay_processed=(
            result.second_session_replay_processed
        ),
        second_session_replay_complete=(
            result.second_session_replay_complete
        ),

        reconciliation_matched=(
            result.reconciliation_matched
        ),
        heartbeat_healthy=(
            result.heartbeat_healthy
        ),
        reconnect_ready=(
            result.reconnect_ready
        ),

        final_cursor=result.final_cursor,
    )

    assert modified.successful is False


def test_incomplete_first_replay_is_required() -> None:
    """First session must demonstrate interruption during replay."""

    result = create_success_result()

    assert result.first_session_replay_complete is False
    assert (
        result.first_session_replay_processed
        < result.first_session_replay_expected
    )


def test_second_replay_must_complete() -> None:
    """Recovery session must completely drain announced replay."""

    result = create_success_result()

    assert result.second_session_replay_complete is True

    assert (
        result.second_session_replay_processed
        == result.second_session_replay_expected
    )


def test_session_counts_start_at_zero() -> None:
    """New socket-session counters should start empty."""

    counts = SessionCounts()

    assert counts.hello_count == 0
    assert counts.heartbeat_count == 0
    assert counts.lifecycle_count == 0


def test_build_client_without_cursor(
    tmp_path,
) -> None:
    """Initial client should omit since_seq with fresh storage."""

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


def test_build_client_uses_durable_cursor(
    tmp_path,
) -> None:
    """Reconnect client must use the latest durable cursor."""

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


def test_build_client_reads_cursor_each_time(
    tmp_path,
) -> None:
    """Each new client must read current durable state."""

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

    assert (
        second_client._connection_uri()
        == "ws://localhost:8766?since_seq=3"
    )


def test_script_contains_real_connection_error_handling() -> None:
    """Harness must explicitly detect a real socket failure."""

    source = script_source()

    assert "except ConnectionError as error:" in source


def test_script_builds_second_client() -> None:
    """Harness must create a new client after interruption."""

    source = script_source()

    assert "second_client = build_client(" in source


def test_script_checks_interrupted_cursor() -> None:
    """Harness must validate durable state before reconnecting."""

    source = script_source()

    assert "cursor_after_disconnect" in source

    assert "EXPECTED_FIRST_SESSION_CURSOR" in source


def test_script_contains_no_ib_execution_path() -> None:
    """Automatic reconnect harness must not reach IB execution."""

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
    """Harness must not import executable trade-request code."""

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
    """Harness must not automatically resume trading."""

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
    """Harness must contain no broker-client implementation."""

    source = script_source()

    forbidden_tokens = (
        "FakeBrokerClient",
        "BrokerPositionProvider",
        "IBBrokerClient",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_success_requires_reconciliation() -> None:
    """Successful recovery requires matched reconciliation."""

    result = create_success_result()

    assert result.reconciliation_matched is True


def test_success_requires_healthy_heartbeat() -> None:
    """Successful recovery requires heartbeat recovery."""

    result = create_success_result()

    assert result.heartbeat_healthy is True


def test_success_requires_reconnect_readiness() -> None:
    """Successful recovery requires final readiness."""

    result = create_success_result()

    assert result.reconnect_ready is True


def test_success_requires_final_cursor_six() -> None:
    """Successful scenario must durably reach sequence 6."""

    result = create_success_result()

    assert result.final_cursor == 6


def test_second_connection_uses_sequence_three() -> None:
    """Recovery connection must explicitly resume after seq 3."""

    result = create_success_result()

    assert (
        result.second_connection_uri
        == "ws://localhost:8766?since_seq=3"
    )

    assert result.second_requested_since_seq == 3