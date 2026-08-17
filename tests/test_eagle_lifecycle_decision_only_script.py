"""Tests for the Eagle lifecycle decision-only safety harness."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from app.communications.eagle_client import EagleClient
from app.event_store import EventStore
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
)
from app.trade_coordinator import TradeCoordinator

from scripts.test_eagle_lifecycle_decision_only import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_QUANTITY,
    DEFAULT_STOP_LOSS_POINTS,
    DEFAULT_SYMBOL,
    LifecycleDecisionResult,
    build_client,
    build_trade_coordinator,
)


def script_source() -> str:
    """Return the complete decision-only harness source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_lifecycle_decision_only.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def create_success_result() -> LifecycleDecisionResult:
    """Create one representative successful result."""

    return LifecycleDecisionResult(
        hello_count=1,
        heartbeat_count=2,
        lifecycle_count=3,
        accepted_event_count=3,
        duplicate_event_count=0,
        out_of_sequence_count=0,
        approved_decision_count=3,
        rejected_decision_count=0,
        trade_request_count=3,
        final_event_cursor=6,
        durable_signal_count=2,
        broker_calls_possible=False,
        order_submission_possible=False,
    )


def test_default_heartbeat_timeout() -> None:
    """Harness should use established heartbeat timeout."""

    assert DEFAULT_HEARTBEAT_TIMEOUT_SECONDS == 45


def test_default_max_messages_is_positive() -> None:
    """Harness must use a bounded message count."""

    assert DEFAULT_MAX_MESSAGES > 0


def test_default_symbol() -> None:
    """Decision harness should use MBT."""

    assert DEFAULT_SYMBOL == "MBT"


def test_default_quantity() -> None:
    """Decision harness should use one contract."""

    assert DEFAULT_QUANTITY == 1


def test_default_stop_loss() -> None:
    """Decision harness should use 500 stop-loss points."""

    assert (
        DEFAULT_STOP_LOSS_POINTS
        == Decimal("500")
    )


def test_result_is_immutable() -> None:
    """Harness result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.lifecycle_count = 99  # type: ignore[misc]


def test_success_result_is_successful() -> None:
    """Representative safe result should pass."""

    result = create_success_result()

    assert result.successful is True


def test_success_requires_no_broker_calls() -> None:
    """Broker capability must make result unsuccessful."""

    result = LifecycleDecisionResult(
        hello_count=1,
        heartbeat_count=2,
        lifecycle_count=3,
        accepted_event_count=3,
        duplicate_event_count=0,
        out_of_sequence_count=0,
        approved_decision_count=3,
        rejected_decision_count=0,
        trade_request_count=3,
        final_event_cursor=6,
        durable_signal_count=2,
        broker_calls_possible=True,
        order_submission_possible=False,
    )

    assert result.successful is False


def test_success_requires_no_order_submission() -> None:
    """Order capability must make result unsuccessful."""

    result = LifecycleDecisionResult(
        hello_count=1,
        heartbeat_count=2,
        lifecycle_count=3,
        accepted_event_count=3,
        duplicate_event_count=0,
        out_of_sequence_count=0,
        approved_decision_count=3,
        rejected_decision_count=0,
        trade_request_count=3,
        final_event_cursor=6,
        durable_signal_count=2,
        broker_calls_possible=False,
        order_submission_possible=True,
    )

    assert result.successful is False


def test_success_requires_request_count_to_match_approvals() -> None:
    """Every approved decision must contain one request."""

    result = LifecycleDecisionResult(
        hello_count=1,
        heartbeat_count=2,
        lifecycle_count=3,
        accepted_event_count=3,
        duplicate_event_count=0,
        out_of_sequence_count=0,
        approved_decision_count=3,
        rejected_decision_count=0,
        trade_request_count=2,
        final_event_cursor=6,
        durable_signal_count=2,
        broker_calls_possible=False,
        order_submission_possible=False,
    )

    assert result.successful is False


def test_build_client_without_cursor(
    tmp_path,
) -> None:
    """Fresh event store should not request replay cursor."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    client = build_client(
        uri="ws://localhost:8765",
        api_key=None,
        event_store=store,
    )

    assert isinstance(
        client,
        EagleClient,
    )

    assert client.since_seq is None

    assert (
        client._connection_uri()
        == "ws://localhost:8765"
    )


def test_build_client_uses_durable_cursor(
    tmp_path,
) -> None:
    """Client should reconnect from durable event cursor."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    store.mark_seq_processed(
        5
    )

    client = build_client(
        uri="ws://localhost:8765",
        api_key=None,
        event_store=store,
    )

    assert client.since_seq == 5

    assert (
        client._connection_uri()
        == "ws://localhost:8765?since_seq=5"
    )


def test_build_client_preserves_api_key(
    tmp_path,
) -> None:
    """Harness should support Eagle authentication."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    client = build_client(
        uri="wss://example.com/ipc/v1/stream",
        api_key="test-key",
        event_store=store,
    )

    assert client.has_api_key is True

    assert (
        client._connection_headers()
        == {
            "x-api-key": "test-key",
        }
    )


def test_build_trade_coordinator(
    tmp_path,
) -> None:
    """Harness should create production TradeCoordinator."""

    coordinator = build_trade_coordinator(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        ),
    )

    assert isinstance(
        coordinator,
        TradeCoordinator,
    )


def test_coordinator_controls_are_resumed(
    tmp_path,
) -> None:
    """Decision layer must be enabled for request construction."""

    coordinator = build_trade_coordinator(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        ),
    )

    assert (
        coordinator.controls.is_paused
        is False
    )


def test_coordinator_settings(
    tmp_path,
) -> None:
    """Coordinator should use harness trading settings."""

    coordinator = build_trade_coordinator(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        ),
    )

    snapshot = (
        coordinator.controls.create_snapshot()
    )

    assert snapshot.symbol == "MBT"
    assert snapshot.quantity == 1

    assert (
        snapshot.stop_loss_points
        == Decimal("500")
    )


def test_coordinator_uses_durable_lifecycle_guard(
    tmp_path,
) -> None:
    """Coordinator must use production lifecycle guard."""

    lifecycle_database = (
        tmp_path
        / "signals.db"
    )

    coordinator = build_trade_coordinator(
        lifecycle_database_path=(
            lifecycle_database
        ),
    )

    guard = (
        coordinator.signal_lifecycle_guard
    )

    assert isinstance(
        guard,
        SignalLifecycleGuard,
    )

    assert (
        guard.database_path
        == lifecycle_database
    )


def test_script_uses_event_processor() -> None:
    """Lifecycle events must pass durable event checks."""

    source = script_source()

    assert (
        "EventProcessor("
        in source
    )

    assert (
        "event_processor.process("
        in source
    )


def test_script_uses_trade_coordinator() -> None:
    """Accepted events must reach production decision layer."""

    source = script_source()

    assert (
        "TradeCoordinator("
        in source
    )

    assert (
        "coordinator.process_event("
        in source
    )


def test_script_uses_signal_lifecycle_guard() -> None:
    """Decision layer must enforce signal lifecycle."""

    source = script_source()

    assert (
        "SignalLifecycleGuard("
        in source
    )


def test_script_uses_trading_controls() -> None:
    """Harness should use production trading settings."""

    source = script_source()

    assert (
        "TradingControls("
        in source
    )


def test_script_handles_hello() -> None:
    """Harness must recognize Eagle hello frames."""

    source = script_source()

    assert (
        "EagleHello"
        in source
    )


def test_script_handles_heartbeat() -> None:
    """Harness must process Eagle heartbeat frames."""

    source = script_source()

    assert (
        "HeartbeatProcessor("
        in source
    )

    assert (
        "heartbeat_processor.process("
        in source
    )


def test_script_handles_lifecycle_events() -> None:
    """Harness must recognize lifecycle messages."""

    source = script_source()

    assert (
        "IncomingLifecycleEvent"
        in source
    )


def test_script_stops_duplicates_before_coordinator() -> None:
    """Duplicate events must not reach decision layer."""

    source = script_source()

    assert (
        "EventProcessStatus.DUPLICATE_EVENT"
        in source
    )

    assert (
        "Duplicate lifecycle event stopped"
        in source
    )


def test_script_stops_out_of_sequence_before_coordinator() -> None:
    """Old sequence events must not reach decision layer."""

    source = script_source()

    assert (
        "EventProcessStatus.OUT_OF_SEQUENCE"
        in source
    )

    assert (
        "Out-of-sequence lifecycle event stopped"
        in source
    )


def test_script_inspects_trade_request() -> None:
    """Approved request should be observable at hard stop."""

    source = script_source()

    assert (
        "decision.trade_request"
        in source
    )

    assert (
        "TradeRequest was NOT sent"
        in source
    )


def test_script_uses_environment_api_key() -> None:
    """API key must come from environment configuration."""

    source = script_source()

    assert (
        "BTS_EAGLE_API_KEY"
        in source
    )


def test_script_contains_no_ib_imports() -> None:
    """Harness must contain no Interactive Brokers path."""

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


def test_script_contains_no_broker_client() -> None:
    """Harness must instantiate no broker client."""

    source = script_source()

    forbidden_tokens = (
        "FakeBrokerClient",
        "BrokerPositionProvider",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_script_contains_no_risk_manager() -> None:
    """Harness must stop before execution risk pipeline."""

    source = script_source()

    assert (
        "RiskManager"
        not in source
    )

    assert (
        "from app.risk_manager import"
        not in source
    )


def test_script_contains_no_order_factory() -> None:
    """Harness must contain no order construction layer."""

    source = script_source()

    assert (
        "OrderFactory"
        not in source
    )

    assert (
        "order_factory"
        not in source
    )


def test_script_contains_no_execution_client() -> None:
    """Harness must contain no execution client."""

    source = script_source()

    assert (
        "ExecutionClient"
        not in source
    )

    assert (
        "execution_client"
        not in source
    )


def test_script_contains_no_resume_manager() -> None:
    """Harness must not contain reconnect trading resume logic."""

    source = script_source()

    assert (
        "ResumeManager"
        not in source
    )


def test_script_does_not_embed_real_api_key() -> None:
    """Harness source must not contain obvious embedded secrets."""

    source = script_source()

    assert "SUPER-SECRET" not in source
    assert "Bearer " not in source