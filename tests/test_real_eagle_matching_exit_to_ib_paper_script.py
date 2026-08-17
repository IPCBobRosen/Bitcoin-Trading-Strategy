"""Safety tests for the matching Eagle exit -> IB paper harness."""

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
from app.execution_ledger import (
    ExecutionStatus,
)
from app.ib_broker_client import IBBrokerClient
from app.signal_lifecycle_guard import (
    SignalLifecycleState,
)

from scripts.test_real_eagle_matching_exit_to_ib_paper import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    DEFAULT_EVENT_DATABASE,
    DEFAULT_EXECUTION_LEDGER,
    DEFAULT_LIFECYCLE_DATABASE,
    EAGLE_API_KEY_ENVIRONMENT_VARIABLE,
    EAGLE_URI,
    EXPECTED_ENTRY_INTENT,
    EXPECTED_EXIT_INTENT,
    IB_CLIENT_ID,
    IB_HOST,
    IB_PORT,
    MAX_ABSOLUTE_POSITION,
    MAX_ORDER_QUANTITY,
    PAPER_QUANTITY,
    SYMBOL,
    TARGET_ENTRY_EVENT_ID,
    TARGET_SIGNAL_ID,
    MatchingExitResult,
    get_mbt_position,
    require_exact_long_position,
    require_flat_after_exit,
    validate_matching_exit_request,
)


def script_source() -> str:
    """Return complete matching-exit harness source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_real_eagle_matching_exit_to_ib_paper.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def create_success_observe_result() -> MatchingExitResult:
    """Create representative successful observe-only state."""

    return MatchingExitResult(
        armed=False,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=2,
        ignored_other_exit_count=1,
        ignored_non_btc_count=1,

        risk_approved=False,
        readiness_passed=False,

        broker_submission_count=0,
        broker_order_id=None,

        final_execution_status=None,
        final_mbt_position=1,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )


def create_success_armed_result() -> MatchingExitResult:
    """Create representative successful armed close state."""

    return MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=2,
        ignored_other_exit_count=1,
        ignored_non_btc_count=1,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=0,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )


def build_exit_request(
    *,
    signal_id: str = TARGET_SIGNAL_ID,
    symbol: str = SYMBOL,
    quantity: int = PAPER_QUANTITY,
    intent: TradeIntent = EXPECTED_EXIT_INTENT,
    environment: Environment = Environment.STAGING,
) -> TradeRequest:
    """Create one matching-exit TradeRequest."""

    return TradeRequest(
        event_id=(
            signal_id + ":exit"
        ),
        signal_id=signal_id,
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=environment,
        intent=intent,
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def test_target_signal_is_exact_known_open_signal() -> None:
    """Harness must target the exact filled Eagle signal."""

    assert (
        TARGET_SIGNAL_ID
        == "bt-1778737920000-BTCUSDT-43"
    )


def test_target_entry_event_matches_signal() -> None:
    """Entry event ID must correspond to the target signal."""

    assert (
        TARGET_ENTRY_EVENT_ID
        == (
            TARGET_SIGNAL_ID
            + ":entry"
        )
    )


def test_expected_entry_intent_is_buy_to_open() -> None:
    """Known position originated from BUY_TO_OPEN."""

    assert (
        EXPECTED_ENTRY_INTENT
        is TradeIntent.BUY_TO_OPEN
    )


def test_expected_exit_intent_is_sell_to_close() -> None:
    """Known long position must close with SELL_TO_CLOSE."""

    assert (
        EXPECTED_EXIT_INTENT
        is TradeIntent.SELL_TO_CLOSE
    )


def test_eagle_uri_is_fund_lane() -> None:
    """Harness should use supplied real Eagle Fund lane."""

    assert (
        EAGLE_URI
        == (
            "wss://tracer.eagleailabs.com/"
            "ipc-api/ipc/v1/fund/stream"
        )
    )


def test_api_key_comes_from_environment() -> None:
    """Eagle credential must remain outside source."""

    assert (
        EAGLE_API_KEY_ENVIRONMENT_VARIABLE
        == "BTS_EAGLE_API_KEY"
    )


def test_tws_targets_paper_port() -> None:
    """Matching exit must remain on TWS paper endpoint."""

    assert IB_HOST == "127.0.0.1"
    assert IB_PORT == 7497
    assert IB_CLIENT_ID == 1


def test_symbol_is_mbt() -> None:
    """Matching close must trade MBT only."""

    assert SYMBOL == "MBT"


def test_quantity_is_one() -> None:
    """Matching close permits exactly one MBT."""

    assert PAPER_QUANTITY == 1


def test_max_order_quantity_is_one() -> None:
    """Risk limit must remain one contract."""

    assert MAX_ORDER_QUANTITY == 1


def test_max_absolute_position_is_one() -> None:
    """Risk limit must remain one absolute MBT."""

    assert MAX_ABSOLUTE_POSITION == 1


def test_contract_month_matches_entry_harness() -> None:
    """Exit must use same MBT contract month."""

    assert CONTRACT_MONTH == "20260828"


def test_durable_paths_reuse_bridge_state() -> None:
    """Exit harness must reuse original bridge databases."""

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


def test_exact_exit_arming_argument() -> None:
    """Exit transmission requires explicit arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-paper-exit"
    )


def test_result_is_immutable() -> None:
    """Matching-exit result must be immutable."""

    result = create_success_observe_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.armed = True  # type: ignore[misc]


def test_observe_result_is_successful() -> None:
    """Valid observe-only matching exit should pass."""

    assert (
        create_success_observe_result().successful
        is True
    )


def test_armed_result_is_successful() -> None:
    """Valid one-order matching close should pass."""

    assert (
        create_success_armed_result().successful
        is True
    )


def test_observe_mode_fails_if_submission_occurs() -> None:
    """Observe-only mode must never tolerate broker submission."""

    result = create_success_observe_result()

    modified = MatchingExitResult(
        armed=False,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=False,
        readiness_passed=False,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=None,
        final_mbt_position=1,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_success_requires_starting_long_open() -> None:
    """Harness must start from durable LONG_OPEN state."""

    result = create_success_armed_result()

    modified = MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=0,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_success_requires_filled_entry_record() -> None:
    """Known entry must be durably FILLED."""

    result = create_success_armed_result()

    modified = MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.SUBMITTED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=0,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_success_requires_exact_initial_position() -> None:
    """Matching exit must begin from exactly +1 MBT."""

    result = create_success_armed_result()

    modified = MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=2,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=0,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_armed_success_requires_target_exit() -> None:
    """Armed result must include exact target exit."""

    result = create_success_armed_result()

    modified = MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=False,
        target_exit_event_id=None,
        target_exit_intent=None,

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=0,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_armed_success_requires_one_submission() -> None:
    """Exactly one broker close must occur."""

    result = create_success_armed_result()

    modified = MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=2,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=0,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_armed_success_requires_flat_final_position() -> None:
    """Successful close must leave MBT exactly flat."""

    result = create_success_armed_result()

    modified = MatchingExitResult(
        armed=True,

        eagle_hello_received=True,
        eagle_environment_staging=True,

        starting_lifecycle_state=(
            SignalLifecycleState.LONG_OPEN
        ),
        entry_execution_status=(
            ExecutionStatus.FILLED
        ),

        initial_position_count=1,
        initial_mbt_position=1,

        target_exit_seen=True,
        target_exit_event_id=(
            TARGET_SIGNAL_ID + ":exit"
        ),
        target_exit_intent=(
            TradeIntent.SELL_TO_CLOSE.value
        ),

        ignored_entry_count=0,
        ignored_other_exit_count=0,
        ignored_non_btc_count=0,

        risk_approved=True,
        readiness_passed=True,

        broker_submission_count=1,
        broker_order_id=10,

        final_execution_status=(
            ExecutionStatus.FILLED
        ),
        final_mbt_position=1,

        final_lifecycle_state=(
            SignalLifecycleState.CLOSED
        ),

        kill_switch_active=False,

        final_eagle_cursor=203700,
    )

    assert modified.successful is False


def test_validate_matching_exit_accepts_exact_request() -> None:
    """Exact SELL_TO_CLOSE request should pass."""

    validate_matching_exit_request(
        build_exit_request()
    )


def test_validate_matching_exit_rejects_wrong_signal() -> None:
    """Exit for another signal must fail closed."""

    request = build_exit_request(
        signal_id="different-signal",
    )

    with pytest.raises(
        RuntimeError,
        match="unexpected signal_id",
    ):
        validate_matching_exit_request(
            request
        )


def test_validate_matching_exit_rejects_wrong_symbol() -> None:
    """Matching exit cannot trade another contract."""

    request = build_exit_request(
        symbol="MES",
    )

    with pytest.raises(
        RuntimeError,
        match="permits MBT only",
    ):
        validate_matching_exit_request(
            request
        )


def test_validate_matching_exit_rejects_quantity_two() -> None:
    """Matching close must remain one contract."""

    request = build_exit_request(
        quantity=2,
    )

    with pytest.raises(
        RuntimeError,
        match="exactly 1 MBT",
    ):
        validate_matching_exit_request(
            request
        )


def test_validate_matching_exit_rejects_open_intent() -> None:
    """Matching close cannot become a new opening order."""

    request = build_exit_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    with pytest.raises(
        RuntimeError,
        match="SELL_TO_CLOSE",
    ):
        validate_matching_exit_request(
            request
        )


def test_validate_matching_exit_rejects_live_environment() -> None:
    """Matching test is restricted to Eagle staging."""

    request = build_exit_request(
        environment=Environment.LIVE,
    )

    with pytest.raises(
        RuntimeError,
        match="Environment.STAGING",
    ):
        validate_matching_exit_request(
            request
        )


def test_get_mbt_position_requires_snapshot() -> None:
    """Broker lookup must fail closed before snapshot completion."""

    broker = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="position snapshot is not complete",
    ):
        get_mbt_position(
            broker
        )


def test_require_exact_long_position_requires_snapshot() -> None:
    """Long-position requirement must fail before completed snapshot."""

    broker = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="position snapshot is not complete",
    ):
        require_exact_long_position(
            broker
        )


def test_require_flat_after_exit_requires_snapshot() -> None:
    """Flat-position check must fail before completed snapshot."""

    broker = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="position snapshot is not complete",
    ):
        require_flat_after_exit(
            broker
        )


def test_script_hard_stops_all_entries() -> None:
    """Matching-exit harness must refuse every entry."""

    source = script_source()

    assert (
        "ENTRY HARD STOP"
        in source
    )

    assert (
        "will not open another position"
        in source
    )


def test_script_hard_stops_unrelated_exits() -> None:
    """Only target signal may close the position."""

    source = script_source()

    assert (
        "UNRELATED EXIT HARD STOP."
        in source
    )

    assert (
        "does not belong to the "
        in source
    )

    assert (
        "target open MBT position."
        in source
    )


def test_script_requires_long_open_lifecycle() -> None:
    """Durable lifecycle must be LONG_OPEN before run."""

    source = script_source()

    assert (
        "SignalLifecycleState.LONG_OPEN"
        in source
    )

    assert (
        "requires target "
        in source
    )

    assert (
        "signal lifecycle to be LONG_OPEN."
        in source
    )


def test_script_requires_filled_entry_record() -> None:
    """Known opening execution must be FILLED."""

    source = script_source()

    assert (
        "validate_existing_entry_record("
        in source
    )

    assert (
        "Existing entry execution is not FILLED."
        in source
    )


def test_script_requires_exact_plus_one_position() -> None:
    """TWS must have exactly one +1 MBT position."""

    source = script_source()

    assert (
        "require_exact_long_position("
        in source
    )

    assert (
        "+1 MBT before the close."
        in source
    )


def test_script_uses_risk_manager() -> None:
    """Matching close must still pass RiskManager."""

    source = script_source()

    assert (
        "RiskManager("
        in source
    )

    assert (
        "risk_manager.evaluate("
        in source
    )


def test_script_requires_projected_flat_position() -> None:
    """Risk projection must return to zero."""

    source = script_source()

    assert (
        "risk_decision.projected_position"
        in source
    )

    assert (
        "project broker position to zero."
        in source
    )


def test_script_uses_ib_readiness() -> None:
    """Matching close must pass IB readiness gate."""

    source = script_source()

    assert (
        "IBTradingReadiness("
        in source
    )

    assert (
        "readiness.require_ready("
        in source
    )


def test_script_submits_only_sell() -> None:
    """IB package must explicitly be a SELL."""

    source = script_source()

    assert (
        '!= "SELL"'
        in source
    )

    assert (
        "exit IB action is not SELL."
        in source
    )


def test_script_has_one_order_limit() -> None:
    """At most one matching close may reach IB."""

    source = script_source()

    assert (
        "one-order limit already reached."
        in source
    )

    assert (
        "ONE-ORDER LIMIT REACHED."
        in source
    )


def test_script_observe_mode_blocks_submission() -> None:
    """Default mode must stop before IB submission."""

    source = script_source()

    assert (
        "OBSERVE-ONLY HARD STOP."
        in source
    )

    assert (
        "NOT sent to Interactive Brokers."
        in source
    )


def test_script_verifies_account_flat_after_fill() -> None:
    """Filled matching exit must reconcile to flat."""

    source = script_source()

    assert (
        "require_flat_after_exit("
        in source
    )

    assert (
        "TWS paper account returned "
        in source
    )

    assert (
        "to flat."
        in source
    )


def test_script_targets_paper_port_not_live_port() -> None:
    """Exit harness must remain on TWS paper port."""

    source = script_source()

    assert (
        "IB_PORT = 7497"
        in source
    )

    assert (
        "IB_PORT = 7496"
        not in source
    )


def test_script_does_not_embed_api_key() -> None:
    """Real Eagle credential must not be embedded."""

    source = script_source()

    assert (
        "BTS_EAGLE_API_KEY"
        in source
    )

    assert "SUPER-SECRET" not in source
    assert "Bearer " not in source