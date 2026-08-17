"""Tests for the real Eagle STAGING decision-only safety harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.communications.eagle_client import EagleClient
from app.communications.eagle_trade_adapter import EagleTradeAdapter
from app.event_store import EventStore
from app.signal_lifecycle_guard import SignalLifecycleGuard
from app.trade_coordinator import TradeCoordinator

from scripts.test_real_eagle_staging_decision_only import (
    DEFAULT_EVENT_DATABASE,
    DEFAULT_LIFECYCLE_DATABASE,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_URI,
    SUPPORTED_DECISION_MESSAGE_TYPES,
    RealEagleStagingDecisionResult,
    build_client,
    build_decision_components,
)


def script_source() -> str:
    """Return the complete real-Eagle decision-only source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_real_eagle_staging_decision_only.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def create_success_result() -> RealEagleStagingDecisionResult:
    """Create one representative safe integration result."""

    return RealEagleStagingDecisionResult(
        hello_count=1,
        heartbeat_count=3,
        lifecycle_count=8,

        accepted_event_count=8,
        duplicate_event_count=0,
        out_of_sequence_count=0,

        btc_adapted_count=6,
        ignored_symbol_count=1,
        ignored_unknown_exit_count=1,
        ignored_other_lifecycle_count=0,

        approved_decision_count=6,
        rejected_decision_count=0,
        trade_request_count=6,

        long_open_count=0,
        short_open_count=0,
        closed_signal_count=3,

        final_event_cursor=202369,

        broker_calls_possible=False,
        order_submission_possible=False,
    )


def test_default_uri_is_real_eagle_fund_lane() -> None:
    """Harness should point to the supplied Eagle Fund lane."""

    assert (
        DEFAULT_URI
        == (
            "wss://tracer.eagleailabs.com/"
            "ipc-api/ipc/v1/fund/stream"
        )
    )


def test_default_event_database_is_isolated() -> None:
    """Real staging events should use a dedicated database."""

    assert (
        DEFAULT_EVENT_DATABASE.name
        == "real_eagle_staging_decision_events.db"
    )


def test_default_lifecycle_database_is_isolated() -> None:
    """Real staging lifecycle state should be isolated."""

    assert (
        DEFAULT_LIFECYCLE_DATABASE.name
        == "real_eagle_staging_decision_signals.db"
    )


def test_default_max_messages_is_positive() -> None:
    """Real staging session should remain bounded."""

    assert DEFAULT_MAX_MESSAGES > 0


def test_only_entry_and_exit_reach_decision_adapter() -> None:
    """Current decision path should explicitly allow entry/exit only."""

    assert SUPPORTED_DECISION_MESSAGE_TYPES == {
        "fund.entry",
        "fund.exit",
    }


def test_result_is_immutable() -> None:
    """Integration result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.hello_count = 99  # type: ignore[misc]


def test_success_result_is_successful() -> None:
    """Representative safe result should pass."""

    result = create_success_result()

    assert result.successful is True


def test_success_requires_no_broker_calls() -> None:
    """Any broker capability must invalidate the result."""

    result = create_success_result()

    modified = RealEagleStagingDecisionResult(
        hello_count=result.hello_count,
        heartbeat_count=result.heartbeat_count,
        lifecycle_count=result.lifecycle_count,

        accepted_event_count=result.accepted_event_count,
        duplicate_event_count=result.duplicate_event_count,
        out_of_sequence_count=result.out_of_sequence_count,

        btc_adapted_count=result.btc_adapted_count,
        ignored_symbol_count=result.ignored_symbol_count,
        ignored_unknown_exit_count=result.ignored_unknown_exit_count,
        ignored_other_lifecycle_count=(
            result.ignored_other_lifecycle_count
        ),

        approved_decision_count=result.approved_decision_count,
        rejected_decision_count=result.rejected_decision_count,
        trade_request_count=result.trade_request_count,

        long_open_count=result.long_open_count,
        short_open_count=result.short_open_count,
        closed_signal_count=result.closed_signal_count,

        final_event_cursor=result.final_event_cursor,

        broker_calls_possible=True,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_success_requires_no_order_submission() -> None:
    """Any order-submission capability must invalidate the result."""

    result = create_success_result()

    modified = RealEagleStagingDecisionResult(
        hello_count=result.hello_count,
        heartbeat_count=result.heartbeat_count,
        lifecycle_count=result.lifecycle_count,

        accepted_event_count=result.accepted_event_count,
        duplicate_event_count=result.duplicate_event_count,
        out_of_sequence_count=result.out_of_sequence_count,

        btc_adapted_count=result.btc_adapted_count,
        ignored_symbol_count=result.ignored_symbol_count,
        ignored_unknown_exit_count=result.ignored_unknown_exit_count,
        ignored_other_lifecycle_count=(
            result.ignored_other_lifecycle_count
        ),

        approved_decision_count=result.approved_decision_count,
        rejected_decision_count=result.rejected_decision_count,
        trade_request_count=result.trade_request_count,

        long_open_count=result.long_open_count,
        short_open_count=result.short_open_count,
        closed_signal_count=result.closed_signal_count,

        final_event_cursor=result.final_event_cursor,

        broker_calls_possible=False,
        order_submission_possible=True,
    )

    assert modified.successful is False


def test_success_requires_request_count_to_match_approvals() -> None:
    """Each approved decision must correspond to one TradeRequest."""

    result = create_success_result()

    modified = RealEagleStagingDecisionResult(
        hello_count=result.hello_count,
        heartbeat_count=result.heartbeat_count,
        lifecycle_count=result.lifecycle_count,

        accepted_event_count=result.accepted_event_count,
        duplicate_event_count=result.duplicate_event_count,
        out_of_sequence_count=result.out_of_sequence_count,

        btc_adapted_count=result.btc_adapted_count,
        ignored_symbol_count=result.ignored_symbol_count,
        ignored_unknown_exit_count=result.ignored_unknown_exit_count,
        ignored_other_lifecycle_count=(
            result.ignored_other_lifecycle_count
        ),

        approved_decision_count=6,
        rejected_decision_count=result.rejected_decision_count,
        trade_request_count=5,

        long_open_count=result.long_open_count,
        short_open_count=result.short_open_count,
        closed_signal_count=result.closed_signal_count,

        final_event_cursor=result.final_event_cursor,

        broker_calls_possible=False,
        order_submission_possible=False,
    )

    assert modified.successful is False


def test_build_client_without_cursor(
    tmp_path: Path,
) -> None:
    """Fresh staging client should omit since_seq."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    client = build_client(
        uri=DEFAULT_URI,
        api_key="test-key",
        event_store=store,
    )

    assert isinstance(
        client,
        EagleClient,
    )

    assert client.since_seq is None

    assert (
        client._connection_uri()
        == DEFAULT_URI
    )


def test_build_client_uses_durable_cursor(
    tmp_path: Path,
) -> None:
    """Reconnect must use current durable Eagle sequence."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    store.mark_seq_processed(
        202309
    )

    client = build_client(
        uri=DEFAULT_URI,
        api_key="test-key",
        event_store=store,
    )

    assert client.since_seq == 202309

    assert (
        client._connection_uri()
        == (
            DEFAULT_URI
            + "?since_seq=202309"
        )
    )


def test_build_client_preserves_api_key(
    tmp_path: Path,
) -> None:
    """Real Eagle client should carry supplied authentication."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    client = build_client(
        uri=DEFAULT_URI,
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


def test_build_decision_components(
    tmp_path: Path,
) -> None:
    """Harness should build production decision components."""

    (
        lifecycle_guard,
        adapter,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        )
    )

    assert isinstance(
        lifecycle_guard,
        SignalLifecycleGuard,
    )

    assert isinstance(
        adapter,
        EagleTradeAdapter,
    )

    assert isinstance(
        coordinator,
        TradeCoordinator,
    )


def test_adapter_and_coordinator_share_lifecycle_guard(
    tmp_path: Path,
) -> None:
    """Entry/exit translation and decision state must use one guard."""

    (
        lifecycle_guard,
        adapter,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        )
    )

    assert (
        adapter.lifecycle_guard
        is lifecycle_guard
    )

    assert (
        coordinator.signal_lifecycle_guard
        is lifecycle_guard
    )


def test_coordinator_is_enabled_for_decision_only_processing(
    tmp_path: Path,
) -> None:
    """Controls should allow TradeRequest construction only."""

    (
        _,
        _,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        )
    )

    assert (
        coordinator.controls.is_paused
        is False
    )


def test_coordinator_uses_mbt_one_contract(
    tmp_path: Path,
) -> None:
    """BTC staging signals should normalize to one MBT request."""

    (
        _,
        _,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            tmp_path
            / "signals.db"
        )
    )

    snapshot = (
        coordinator.controls.create_snapshot()
    )

    assert snapshot.symbol == "MBT"
    assert snapshot.quantity == 1


def test_script_requires_environment_api_key() -> None:
    """Real Eagle authentication must come from environment."""

    source = script_source()

    assert "BTS_EAGLE_API_KEY" in source


def test_script_checks_staging_environment() -> None:
    """Harness must reject a non-staging hello."""

    source = script_source()

    assert (
        'message.environment.value'
        in source
    )

    assert (
        '"Safety violation: real-Eagle decision-only "'
        in source
    )


def test_script_uses_event_processor() -> None:
    """Real lifecycle events must pass durable event checks."""

    source = script_source()

    assert "EventProcessor(" in source
    assert "event_processor.process(" in source


def test_script_uses_btc_adapter() -> None:
    """Real Eagle wire format must pass through adapter."""

    source = script_source()

    assert "EagleTradeAdapter(" in source
    assert "adapter.adapt(" in source


def test_script_uses_trade_coordinator() -> None:
    """Adapted BTC events should reach production decision layer."""

    source = script_source()

    assert "TradeCoordinator(" in source
    assert "coordinator.process_event(" in source


def test_script_ignores_non_btc_symbols() -> None:
    """Non-BTC symbols must remain outside decision layer."""

    source = script_source()

    assert (
        "EagleTradeAdaptStatus.IGNORED_SYMBOL"
        in source
    )

    assert (
        "Non-BTC instrument ignored."
        in source
    )


def test_script_ignores_unknown_exits() -> None:
    """Unknown exits must remain outside decision layer."""

    source = script_source()

    assert (
        "EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT"
        in source
    )


def test_script_stops_duplicates_before_adapter() -> None:
    """Duplicate Eagle events must stop before translation."""

    source = script_source()

    assert (
        "EventProcessStatus.DUPLICATE_EVENT"
        in source
    )

    assert (
        "Duplicate stopped before adapter."
        in source
    )


def test_script_stops_out_of_sequence_before_adapter() -> None:
    """Old sequence events must stop before translation."""

    source = script_source()

    assert (
        "EventProcessStatus.OUT_OF_SEQUENCE"
        in source
    )

    assert (
        "Out-of-sequence event stopped"
        in source
    )


def test_script_contains_no_ib_execution_imports() -> None:
    """Harness must not import Interactive Brokers execution modules."""

    source = script_source()

    forbidden_imports = (
        "from app.ib_execution_client import",
        "import app.ib_execution_client",
        "from app.ib_order_factory import",
        "import app.ib_order_factory",
        "from app.ib_api_position_app import",
        "import app.ib_api_position_app",
    )

    for forbidden_import in forbidden_imports:
        assert forbidden_import not in source


def test_script_contains_no_risk_execution_pipeline() -> None:
    """Harness must stop before downstream execution/risk routing."""

    source = script_source()

    assert (
        "from app.risk_manager import"
        not in source
    )

    assert (
        "import app.risk_manager"
        not in source
    )


def test_script_contains_no_order_submission_call() -> None:
    """Harness source must contain no order-submission call."""

    source = script_source()

    forbidden_calls = (
        "placeOrder(",
        "place_order(",
        "submit_order(",
    )

    for forbidden_call in forbidden_calls:
        assert forbidden_call not in source


def test_script_contains_no_broker_client_import() -> None:
    """No broker-client implementation may enter this harness."""

    source = script_source()

    forbidden_imports = (
        "IBExecutionClient",
        "IBBrokerClient",
        "BrokerPositionProvider",
    )

    for forbidden_import in forbidden_imports:
        assert forbidden_import not in source


def test_script_contains_explicit_hard_stop() -> None:
    """Source should visibly preserve the decision-only boundary."""

    source = script_source()

    assert (
        "HARD STOP - nothing sent to a broker "
        "or execution client."
        in source
    )


def test_script_does_not_embed_real_api_key() -> None:
    """Staging secret must never be embedded in source."""

    source = script_source()

    assert "SUPER-SECRET" not in source
    assert "Bearer " not in source