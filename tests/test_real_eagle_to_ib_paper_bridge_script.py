"""Safety tests for the real Eagle -> IB paper bridge harness."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.execution_ledger import ExecutionStatus
from app.ib_broker_client import IBBrokerClient
from app.trading_controls import TradingControls

from scripts.test_real_eagle_to_ib_paper_bridge import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    DEFAULT_EVENT_DATABASE,
    DEFAULT_EXECUTION_LEDGER,
    DEFAULT_LIFECYCLE_DATABASE,
    DEFAULT_MAX_MESSAGES,
    EAGLE_API_KEY_ENVIRONMENT_VARIABLE,
    EAGLE_URI,
    IB_CLIENT_ID,
    IB_HOST,
    IB_PORT,
    MAX_ABSOLUTE_POSITION,
    MAX_ORDER_QUANTITY,
    PAPER_QUANTITY,
    SYMBOL,
    RealEagleToIBBridgeResult,
    build_decision_components,
    get_mbt_position,
    require_completely_flat,
    validate_live_trade_request,
)


def script_source() -> str:
    """Return complete bridge source for static safety inspection."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_real_eagle_to_ib_paper_bridge.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def create_success_observe_result() -> RealEagleToIBBridgeResult:
    """Create one representative successful observe-only result."""

    return RealEagleToIBBridgeResult(
        armed=False,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )


def create_success_armed_result() -> RealEagleToIBBridgeResult:
    """Create one representative successful armed paper result."""

    return RealEagleToIBBridgeResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=9,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=9,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=1,

        broker_submission_count=1,

        submitted_event_id="live-event-001",
        submitted_intent="BUY_TO_OPEN",
        broker_order_id=10,

        risk_approved=True,
        readiness_passed=True,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=1,

        kill_switch_active=False,

        final_eagle_cursor=202801,
    )


def build_request(
    *,
    intent: TradeIntent,
    symbol: str = "MBT",
    quantity: int = 1,
) -> TradeRequest:
    """Build one staging TradeRequest for bridge-policy tests."""

    return TradeRequest(
        event_id="event-001",
        signal_id="signal-001",
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=intent,
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def test_real_eagle_uri_is_staging_fund_lane() -> None:
    """Bridge should use the supplied real Eagle Fund lane."""

    assert (
        EAGLE_URI
        == (
            "wss://tracer.eagleailabs.com/"
            "ipc-api/ipc/v1/fund/stream"
        )
    )


def test_api_key_comes_from_environment() -> None:
    """Bridge must never require embedded Eagle credentials."""

    assert (
        EAGLE_API_KEY_ENVIRONMENT_VARIABLE
        == "BTS_EAGLE_API_KEY"
    )


def test_ib_connection_targets_paper_port() -> None:
    """Bridge should target the established TWS paper endpoint."""

    assert IB_HOST == "127.0.0.1"
    assert IB_PORT == 7497
    assert IB_CLIENT_ID == 1


def test_bridge_symbol_is_mbt() -> None:
    """First bridge must trade MBT only."""

    assert SYMBOL == "MBT"


def test_bridge_quantity_is_one() -> None:
    """First bridge permits one contract only."""

    assert PAPER_QUANTITY == 1


def test_max_order_quantity_is_one() -> None:
    """Risk layer must not permit multi-contract opening orders."""

    assert MAX_ORDER_QUANTITY == 1


def test_max_absolute_position_is_one() -> None:
    """Risk layer must not permit pyramiding."""

    assert MAX_ABSOLUTE_POSITION == 1


def test_contract_month_matches_current_paper_harness() -> None:
    """Bridge should preserve the proven MBT contract."""

    assert CONTRACT_MONTH == "20260828"


def test_default_max_messages_is_positive() -> None:
    """Bridge observation session must remain bounded."""

    assert DEFAULT_MAX_MESSAGES > 0


def test_persistent_databases_are_isolated() -> None:
    """Bridge should use dedicated durable state files."""

    assert (
        DEFAULT_EVENT_DATABASE.name
        == "real_eagle_to_ib_bridge_events.db"
    )

    assert (
        DEFAULT_LIFECYCLE_DATABASE.name
        == "real_eagle_to_ib_bridge_signals.db"
    )

    assert (
        DEFAULT_EXECUTION_LEDGER.name
        == "real_eagle_to_ib_bridge_execution.db"
    )


def test_exact_arming_argument() -> None:
    """Paper transmission must require an explicit arming switch."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-paper-order"
    )


def test_result_is_immutable() -> None:
    """Bridge result should be immutable."""

    result = create_success_observe_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.armed = True  # type: ignore[misc]


def test_observe_result_is_successful() -> None:
    """Valid observe-only run should pass with zero submission."""

    result = create_success_observe_result()

    assert result.successful is True


def test_armed_result_is_successful() -> None:
    """Valid single-order paper result should pass."""

    result = create_success_armed_result()

    assert result.successful is True


def test_observe_mode_fails_if_any_submission_occurs() -> None:
    """Observe-only mode must never tolerate broker submission."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=False,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=1,

        submitted_event_id="unexpected",
        submitted_intent="BUY_TO_OPEN",
        broker_order_id=10,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_success_requires_staging() -> None:
    """Bridge must fail closed outside Eagle staging."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=result.armed,

        eagle_hello_received=True,
        eagle_environment_staging=False,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_success_requires_eagle_open_count_zero() -> None:
    """First bridge must refuse pre-existing Eagle opens."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=result.armed,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=1,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_success_requires_flat_paper_account() -> None:
    """Bridge must start with no existing paper positions."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=result.armed,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=1,
        initial_mbt_position=1,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_success_requires_replay_complete() -> None:
    """Broker bridge must not pass while replay is incomplete."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=result.armed,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=9,
        replay_complete=False,
        post_replay_heartbeat_seen=False,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_success_requires_post_replay_heartbeat() -> None:
    """Replay completion alone must not unlock broker execution."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=result.armed,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=False,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_success_requires_kill_switch_inactive() -> None:
    """Emergency kill state must fail the bridge result."""

    result = create_success_observe_result()

    modified = RealEagleToIBBridgeResult(
        armed=result.armed,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=8,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=8,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=0,

        broker_submission_count=0,

        submitted_event_id=None,
        submitted_intent=None,
        broker_order_id=None,

        risk_approved=False,
        readiness_passed=False,

        final_execution_status=None,
        final_mbt_position=0,

        kill_switch_active=True,

        final_eagle_cursor=202800,
    )

    assert modified.successful is False


def test_armed_success_requires_exactly_one_submission() -> None:
    """Armed mode must never report success with two submissions."""

    result = create_success_armed_result()

    modified = RealEagleToIBBridgeResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=9,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=9,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=2,

        broker_submission_count=2,

        submitted_event_id=(
            result.submitted_event_id
        ),
        submitted_intent=(
            result.submitted_intent
        ),
        broker_order_id=(
            result.broker_order_id
        ),

        risk_approved=True,
        readiness_passed=True,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=1,

        kill_switch_active=False,

        final_eagle_cursor=202801,
    )

    assert modified.successful is False


def test_armed_success_requires_filled_status() -> None:
    """Armed bridge must finish with a confirmed fill."""

    result = create_success_armed_result()

    modified = RealEagleToIBBridgeResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=9,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=9,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=1,

        broker_submission_count=1,

        submitted_event_id="live-event-001",
        submitted_intent="BUY_TO_OPEN",
        broker_order_id=10,

        risk_approved=True,
        readiness_passed=True,

        final_execution_status=(
            ExecutionStatus.REJECTED
        ),
        final_mbt_position=0,

        kill_switch_active=False,

        final_eagle_cursor=202801,
    )

    assert modified.successful is False


def test_armed_success_requires_one_unit_resulting_position() -> None:
    """Successful first bridge must end at +/-1 MBT."""

    result = create_success_armed_result()

    modified = RealEagleToIBBridgeResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,
        eagle_open_count=0,

        initial_broker_position_count=0,
        initial_mbt_position=0,

        replay_expected=10,
        replay_processed=10,
        replay_complete=True,
        post_replay_heartbeat_seen=True,

        btc_events_adapted=9,
        non_btc_entries_ignored=1,
        unknown_exits_ignored=1,

        approved_trade_decisions=9,
        rejected_trade_decisions=0,

        live_eligible_trade_requests=1,

        broker_submission_count=1,

        submitted_event_id="live-event-001",
        submitted_intent="BUY_TO_OPEN",
        broker_order_id=10,

        risk_approved=True,
        readiness_passed=True,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=2,

        kill_switch_active=False,

        final_eagle_cursor=202801,
    )

    assert modified.successful is False


def test_build_decision_components_share_controls_and_guard(
    tmp_path: Path,
) -> None:
    """Adapter/coordinator must use one lifecycle guard."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    (
        guard,
        adapter,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            tmp_path / "signals.db"
        ),
        trading_controls=controls,
    )

    assert (
        adapter.lifecycle_guard
        is guard
    )

    assert (
        coordinator.signal_lifecycle_guard
        is guard
    )

    assert (
        coordinator.controls
        is controls
    )


def test_validate_live_request_accepts_one_mbt_long_open() -> None:
    """One MBT BUY_TO_OPEN staging request is permitted."""

    request = build_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    validate_live_trade_request(
        request
    )


def test_validate_live_request_accepts_one_mbt_short_open() -> None:
    """One MBT SELL_TO_OPEN staging request is permitted."""

    request = build_request(
        intent=TradeIntent.SELL_TO_OPEN,
    )

    validate_live_trade_request(
        request
    )


def test_validate_live_request_rejects_wrong_symbol() -> None:
    """Bridge must not submit a non-MBT request."""

    request = build_request(
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MES",
    )

    with pytest.raises(
        RuntimeError,
        match="permits MBT only",
    ):
        validate_live_trade_request(
            request
        )


def test_validate_live_request_rejects_quantity_two() -> None:
    """Bridge must not submit two contracts."""

    request = build_request(
        intent=TradeIntent.BUY_TO_OPEN,
        quantity=2,
    )

    with pytest.raises(
        RuntimeError,
        match="exactly 1 MBT",
    ):
        validate_live_trade_request(
            request
        )


def test_require_completely_flat_requires_completed_snapshot() -> None:
    """Broker position safety must fail closed before snapshot completes."""

    broker = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="position snapshot is not complete",
    ):
        require_completely_flat(
            broker
        )


def test_get_mbt_position_requires_completed_snapshot() -> None:
    """Position lookup must fail closed before snapshot completion."""

    broker = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="position snapshot is not complete",
    ):
        get_mbt_position(
            broker
        )


def test_script_requires_empty_execution_ledger() -> None:
    """Controlled bridge must refuse existing execution history."""

    source = script_source()

    assert (
        "Bridge execution ledger must be empty"
        in source
    )


def test_script_requires_flat_broker() -> None:
    """Actual broker position must be checked before submission."""

    source = script_source()

    assert (
        "require_completely_flat("
        in source
    )

    assert (
        "TWS paper account to start flat."
        in source
    )


def test_script_blocks_historical_replay_submission() -> None:
    """Replay signals must never reach execution."""

    source = script_source()

    assert (
        "REPLAY HARD STOP"
        in source
    )

    assert (
        "historical signal cannot"
        in source
    )

    assert (
        "reach broker submission."
        in source
    )


def test_script_requires_post_replay_heartbeat() -> None:
    """Live submission must require fresh Eagle health."""

    source = script_source()

    assert (
        "post_replay_heartbeat_seen"
        in source
    )

    assert (
        "LIVE HARD STOP - no "
        in source
    )

    assert (
        "post-replay heartbeat yet."
        in source
    )


def test_script_observe_mode_does_not_submit() -> None:
    """Default mode must explicitly stop before execution."""

    source = script_source()

    assert (
        "OBSERVE-ONLY HARD STOP."
        in source
    )

    assert (
        "IBExecutionClient.submit "
        in source
    )

    assert (
        "was NOT called."
        in source
    )


def test_script_contains_one_order_limit() -> None:
    """At most one broker submission may occur."""

    source = script_source()

    assert (
        "one-order maximum "
        in source
    )

    assert (
        "already reached."
        in source
    )

    assert (
        "ONE-ORDER LIMIT REACHED."
        in source
    )


def test_script_uses_risk_manager() -> None:
    """Bridge must pass TradeRequest through RiskManager."""

    source = script_source()

    assert (
        "RiskManager("
        in source
    )

    assert (
        "risk_manager.evaluate("
        in source
    )


def test_script_uses_ib_trading_readiness() -> None:
    """IB readiness gate must run before execution."""

    source = script_source()

    assert (
        "IBTradingReadiness("
        in source
    )

    assert (
        "readiness.require_ready("
        in source
    )


def test_script_uses_execution_client() -> None:
    """Armed bridge should use proven IBExecutionClient."""

    source = script_source()

    assert (
        "IBExecutionClient("
        in source
    )

    assert (
        "execution_client.submit("
        in source
    )


def test_script_uses_market_order_factory() -> None:
    """First bridge uses controlled DAY market order."""

    source = script_source()

    assert (
        'order_type="MKT"'
        in source
    )

    assert (
        'time_in_force="DAY"'
        in source
    )

    assert (
        "transmit=True"
        in source
    )


def test_script_checks_expected_ib_action() -> None:
    """BUY/SELL IB action must match normalized Eagle intent."""

    source = script_source()

    assert (
        "expected_action = ("
        in source
    )

    assert '"BUY"' in source
    assert '"SELL"' in source


def test_script_uses_environment_api_key() -> None:
    """Eagle key must remain outside source."""

    source = script_source()

    assert (
        "BTS_EAGLE_API_KEY"
        in source
    )


def test_script_does_not_embed_real_api_key() -> None:
    """No obvious credential may be embedded."""

    source = script_source()

    assert "SUPER-SECRET" not in source
    assert "Bearer " not in source


def test_script_targets_tws_paper_port_only() -> None:
    """First integrated bridge must remain on paper port."""

    source = script_source()

    assert (
        "IB_PORT = 7497"
        in source
    )

    assert (
        "IB_PORT = 7496"
        not in source
    )


def test_script_does_not_allow_position_above_one() -> None:
    """First integrated bridge must hard-code one-unit exposure."""

    source = script_source()

    assert (
        "MAX_ABSOLUTE_POSITION = 1"
        in source
    )

    assert (
        "MAX_ORDER_QUANTITY = 1"
        in source
    )