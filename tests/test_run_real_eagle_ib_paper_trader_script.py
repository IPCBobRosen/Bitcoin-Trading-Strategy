"""Safety tests for the continuous Eagle -> IB paper trader."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.broker_position_adapter import RawBrokerPosition
from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionStatus,
)
from app.ib_broker_client import (
    IBBrokerClient,
    IBPositionRecord,
)
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)

from scripts.run_real_eagle_ib_paper_trader import (
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
    DurableOpenSignal,
    execution_state_clear,
    expected_ib_action,
    expected_position_after_trade,
    get_mbt_position,
    load_durable_open_signals,
    reconcile_broker_and_lifecycle,
    require_execution_state_clear,
    require_no_other_broker_positions,
    validate_trade_request_against_position,
)


def script_source() -> str:
    """Return complete continuous-runner source."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_real_eagle_ib_paper_trader.py"
    )

    return source_path.read_text(
        encoding="utf-8"
    )


def build_trade_request(
    *,
    intent: TradeIntent,
    signal_id: str = "signal-001",
    symbol: str = "MBT",
    quantity: int = 1,
) -> TradeRequest:
    """Create one staging TradeRequest for policy tests."""

    return TradeRequest(
        event_id=(
            signal_id
            + ":event"
        ),
        signal_id=signal_id,
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=intent,
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def build_completed_broker(
    *,
    positions: tuple[
        IBPositionRecord,
        ...,
    ] = (),
) -> IBBrokerClient:
    """Build one completed synthetic IB position snapshot."""

    broker = IBBrokerClient()

    broker.begin_position_snapshot()

    for position in positions:
        broker.receive_position(
            position
        )

    broker.finish_position_snapshot()

    return broker


def create_lifecycle_database(
    tmp_path: Path,
    *,
    signal_id: str,
    intent: TradeIntent,
) -> Path:
    """Create one durable lifecycle transition."""

    path = (
        tmp_path
        / "signals.db"
    )

    guard = SignalLifecycleGuard(
        path
    )

    request = build_trade_request(
        intent=intent,
        signal_id=signal_id,
    )

    decision = guard.process(
        request
    )

    assert decision.allowed is True

    return path


def create_filled_execution(
    tmp_path: Path,
) -> ExecutionLedger:
    """Create a ledger containing one terminal filled execution."""

    path = (
        tmp_path
        / "execution.db"
    )

    ledger = ExecutionLedger(
        path
    )

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    ledger.reserve(
        request
    )

    ledger.mark_submitted(
        request.event_id,
        broker_order_id=10,
    )

    ledger.mark_filled(
        request.event_id
    )

    return ledger


def test_eagle_uri_is_expected_fund_lane() -> None:
    """Continuous runner should use Eagle Fund WebSocket."""

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


def test_runner_targets_tws_paper_port() -> None:
    """Continuous runner must use TWS paper endpoint."""

    assert IB_HOST == "127.0.0.1"
    assert IB_PORT == 7497
    assert IB_CLIENT_ID == 1


def test_runner_trades_mbt_only() -> None:
    """Continuous runner must remain restricted to MBT."""

    assert SYMBOL == "MBT"


def test_runner_quantity_is_one() -> None:
    """Continuous runner must use one contract."""

    assert PAPER_QUANTITY == 1


def test_max_order_quantity_is_one() -> None:
    """Opening-order risk limit must remain one."""

    assert MAX_ORDER_QUANTITY == 1


def test_max_absolute_position_is_one() -> None:
    """Pyramiding must remain disabled."""

    assert MAX_ABSOLUTE_POSITION == 1


def test_contract_month_matches_paper_harness() -> None:
    """Continuous runner must use proven MBT contract."""

    assert CONTRACT_MONTH == "20260828"


def test_default_message_limit_is_continuous() -> None:
    """Zero should represent continuous operation."""

    assert DEFAULT_MAX_MESSAGES == 0


def test_exact_arming_argument() -> None:
    """Continuous broker execution must require explicit arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-continuous-paper"
    )


def test_durable_paths_reuse_proven_bridge_state() -> None:
    """Continuous runner should reuse reconciled durable databases."""

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


def test_get_mbt_position_flat_snapshot() -> None:
    """Completed empty broker snapshot should report zero."""

    broker = build_completed_broker()

    assert (
        get_mbt_position(
            broker
        )
        == 0
    )


def test_get_mbt_position_long_one() -> None:
    """Completed +1 MBT snapshot should report +1."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=1,
            ),
        )
    )

    assert (
        get_mbt_position(
            broker
        )
        == 1
    )


def test_get_mbt_position_short_one() -> None:
    """Completed -1 MBT snapshot should report -1."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=-1,
            ),
        )
    )

    assert (
        get_mbt_position(
            broker
        )
        == -1
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


def test_require_no_other_positions_accepts_flat() -> None:
    """Flat broker state should pass."""

    broker = build_completed_broker()

    require_no_other_broker_positions(
        broker
    )


def test_require_no_other_positions_accepts_mbt() -> None:
    """One MBT position should pass."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=1,
            ),
        )
    )

    require_no_other_broker_positions(
        broker
    )


def test_require_no_other_positions_rejects_unrelated_contract() -> None:
    """Any unrelated broker position must fail closed."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MES",
                local_symbol="MESU6",
                position=1,
            ),
        )
    )

    with pytest.raises(
        RuntimeError,
        match="no unrelated broker positions",
    ):
        require_no_other_broker_positions(
            broker
        )


def test_load_durable_open_signals_empty_database(
    tmp_path: Path,
) -> None:
    """Empty lifecycle database should have no open signals."""

    path = (
        tmp_path
        / "signals.db"
    )

    SignalLifecycleGuard(
        path
    )

    assert (
        load_durable_open_signals(
            path
        )
        == ()
    )


def test_load_durable_open_signals_long(
    tmp_path: Path,
) -> None:
    """LONG_OPEN lifecycle should be returned."""

    path = create_lifecycle_database(
        tmp_path,
        signal_id="long-001",
        intent=TradeIntent.BUY_TO_OPEN,
    )

    signals = (
        load_durable_open_signals(
            path
        )
    )

    assert len(signals) == 1
    assert signals[0].signal_id == "long-001"
    assert (
        signals[0].state
        is SignalLifecycleState.LONG_OPEN
    )


def test_load_durable_open_signals_short(
    tmp_path: Path,
) -> None:
    """SHORT_OPEN lifecycle should be returned."""

    path = create_lifecycle_database(
        tmp_path,
        signal_id="short-001",
        intent=TradeIntent.SELL_TO_OPEN,
    )

    signals = (
        load_durable_open_signals(
            path
        )
    )

    assert len(signals) == 1
    assert signals[0].signal_id == "short-001"
    assert (
        signals[0].state
        is SignalLifecycleState.SHORT_OPEN
    )


def test_reconcile_flat_broker_and_flat_lifecycle(
    tmp_path: Path,
) -> None:
    """Flat broker + no open lifecycle should reconcile."""

    broker = build_completed_broker()

    lifecycle_path = (
        tmp_path
        / "signals.db"
    )

    SignalLifecycleGuard(
        lifecycle_path
    )

    position, signals = (
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )
    )

    assert position == 0
    assert signals == ()


def test_reconcile_long_broker_and_long_lifecycle(
    tmp_path: Path,
) -> None:
    """+1 MBT should reconcile with LONG_OPEN."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=1,
            ),
        )
    )

    lifecycle_path = (
        create_lifecycle_database(
            tmp_path,
            signal_id="long-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    position, signals = (
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )
    )

    assert position == 1
    assert len(signals) == 1
    assert (
        signals[0].state
        is SignalLifecycleState.LONG_OPEN
    )


def test_reconcile_short_broker_and_short_lifecycle(
    tmp_path: Path,
) -> None:
    """-1 MBT should reconcile with SHORT_OPEN."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=-1,
            ),
        )
    )

    lifecycle_path = (
        create_lifecycle_database(
            tmp_path,
            signal_id="short-001",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    position, signals = (
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )
    )

    assert position == -1
    assert len(signals) == 1
    assert (
        signals[0].state
        is SignalLifecycleState.SHORT_OPEN
    )


def test_reconcile_rejects_broker_position_without_open_signal(
    tmp_path: Path,
) -> None:
    """Broker exposure without BTS lifecycle must fail closed."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=1,
            ),
        )
    )

    lifecycle_path = (
        tmp_path
        / "signals.db"
    )

    SignalLifecycleGuard(
        lifecycle_path
    )

    with pytest.raises(
        RuntimeError,
        match="broker has MBT position",
    ):
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )


def test_reconcile_rejects_open_signal_while_broker_flat(
    tmp_path: Path,
) -> None:
    """Durable exposure without broker exposure must fail closed."""

    broker = build_completed_broker()

    lifecycle_path = (
        create_lifecycle_database(
            tmp_path,
            signal_id="long-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="broker is flat",
    ):
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )


def test_reconcile_rejects_long_broker_with_short_lifecycle(
    tmp_path: Path,
) -> None:
    """+1 MBT cannot reconcile with SHORT_OPEN."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=1,
            ),
        )
    )

    lifecycle_path = (
        create_lifecycle_database(
            tmp_path,
            signal_id="short-001",
            intent=TradeIntent.SELL_TO_OPEN,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="not LONG_OPEN",
    ):
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )


def test_reconcile_rejects_short_broker_with_long_lifecycle(
    tmp_path: Path,
) -> None:
    """-1 MBT cannot reconcile with LONG_OPEN."""

    broker = build_completed_broker(
        positions=(
            IBPositionRecord(
                account="DU123",
                symbol="MBT",
                local_symbol="MBTQ6",
                position=-1,
            ),
        )
    )

    lifecycle_path = (
        create_lifecycle_database(
            tmp_path,
            signal_id="long-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="not SHORT_OPEN",
    ):
        reconcile_broker_and_lifecycle(
            broker_client=broker,
            lifecycle_database_path=(
                lifecycle_path
            ),
        )


def test_execution_state_clear_empty_ledger(
    tmp_path: Path,
) -> None:
    """Empty ledger should be clear."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution.db"
    )

    assert (
        execution_state_clear(
            ledger
        )
        is True
    )


def test_execution_state_clear_filled_ledger(
    tmp_path: Path,
) -> None:
    """Filled historical execution should be clear."""

    ledger = create_filled_execution(
        tmp_path
    )

    assert (
        execution_state_clear(
            ledger
        )
        is True
    )


def test_execution_state_clear_reserved_is_false(
    tmp_path: Path,
) -> None:
    """Reserved execution is unresolved."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution.db"
    )

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    ledger.reserve(
        request
    )

    assert (
        execution_state_clear(
            ledger
        )
        is False
    )


def test_require_execution_state_clear_rejects_unresolved(
    tmp_path: Path,
) -> None:
    """Unresolved execution state must block trading."""

    ledger = ExecutionLedger(
        tmp_path
        / "execution.db"
    )

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    ledger.reserve(
        request
    )

    with pytest.raises(
        RuntimeError,
        match="unresolved execution state",
    ):
        require_execution_state_clear(
            ledger
        )


def test_validate_buy_to_open_when_flat() -> None:
    """BUY_TO_OPEN should be valid from flat."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    validate_trade_request_against_position(
        trade_request=request,
        broker_position=0,
        open_signals=(),
    )


def test_validate_sell_to_open_when_flat() -> None:
    """SELL_TO_OPEN should be valid from flat."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_OPEN,
    )

    validate_trade_request_against_position(
        trade_request=request,
        broker_position=0,
        open_signals=(),
    )


def test_validate_open_rejected_while_positioned() -> None:
    """New opening request must fail when broker is positioned."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-old",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-old:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="broker is not flat",
    ):
        validate_trade_request_against_position(
            trade_request=request,
            broker_position=1,
            open_signals=(
                open_signal,
            ),
        )


def test_validate_sell_to_close_matching_long() -> None:
    """Matching SELL_TO_CLOSE should pass from +1."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="long-001",
    )

    open_signal = DurableOpenSignal(
        signal_id="long-001",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="long-001:entry",
    )

    validate_trade_request_against_position(
        trade_request=request,
        broker_position=1,
        open_signals=(
            open_signal,
        ),
    )


def test_validate_buy_to_close_matching_short() -> None:
    """Matching BUY_TO_CLOSE should pass from -1."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_CLOSE,
        signal_id="short-001",
    )

    open_signal = DurableOpenSignal(
        signal_id="short-001",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="short-001:entry",
    )

    validate_trade_request_against_position(
        trade_request=request,
        broker_position=-1,
        open_signals=(
            open_signal,
        ),
    )


def test_validate_close_rejects_wrong_signal_id() -> None:
    """Close must match the durable open signal."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="different",
    )

    open_signal = DurableOpenSignal(
        signal_id="long-001",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="long-001:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="does not match",
    ):
        validate_trade_request_against_position(
            trade_request=request,
            broker_position=1,
            open_signals=(
                open_signal,
            ),
        )


def test_validate_wrong_symbol_rejected() -> None:
    """Continuous trader cannot submit another symbol."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MES",
    )

    with pytest.raises(
        RuntimeError,
        match="permits MBT only",
    ):
        validate_trade_request_against_position(
            trade_request=request,
            broker_position=0,
            open_signals=(),
        )


def test_validate_quantity_two_rejected() -> None:
    """Continuous trader cannot submit two contracts."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
        quantity=2,
    )

    with pytest.raises(
        RuntimeError,
        match="exactly 1 MBT",
    ):
        validate_trade_request_against_position(
            trade_request=request,
            broker_position=0,
            open_signals=(),
        )


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (
            TradeIntent.BUY_TO_OPEN,
            1,
        ),
        (
            TradeIntent.SELL_TO_OPEN,
            -1,
        ),
        (
            TradeIntent.SELL_TO_CLOSE,
            0,
        ),
        (
            TradeIntent.BUY_TO_CLOSE,
            0,
        ),
    ],
)
def test_expected_position_after_trade(
    intent: TradeIntent,
    expected: int,
) -> None:
    """Post-fill position should map correctly by intent."""

    request = build_trade_request(
        intent=intent,
    )

    assert (
        expected_position_after_trade(
            request
        )
        == expected
    )


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (
            TradeIntent.BUY_TO_OPEN,
            "BUY",
        ),
        (
            TradeIntent.BUY_TO_CLOSE,
            "BUY",
        ),
        (
            TradeIntent.SELL_TO_OPEN,
            "SELL",
        ),
        (
            TradeIntent.SELL_TO_CLOSE,
            "SELL",
        ),
    ],
)
def test_expected_ib_action(
    intent: TradeIntent,
    expected: str,
) -> None:
    """IB action should match trade intent."""

    request = build_trade_request(
        intent=intent,
    )

    assert (
        expected_ib_action(
            request
        )
        == expected
    )


def test_script_requires_post_replay_heartbeat() -> None:
    """Live execution must wait for fresh Eagle health."""

    source = script_source()

    assert (
        "post_replay_heartbeat_seen"
        in source
    )

    assert (
        "arrived before required "
        in source
    )

    assert (
        "post-replay heartbeat."
        in source
    )


def test_script_blocks_replay_execution() -> None:
    """Historical replay must never reach IB submission."""

    source = script_source()

    assert (
        "REPLAY HARD STOP"
        in source
    )

    assert (
        "historical event cannot "
        in source
    )

    assert (
        "reach IB execution."
        in source
    )


def test_script_rejects_nonzero_eagle_open_count() -> None:
    """First continuous runner must not join mid-position."""

    source = script_source()

    assert (
        "message.open_count"
        in source
    )

    assert (
        "open_count "
        in source
    )

    assert (
        "is non-zero."
        in source
    )


def test_script_has_observe_only_boundary_before_coordinator() -> None:
    """Unarmed live mode must stop before TradeCoordinator mutation."""

    source = script_source()

    boundary_index = source.index(
        "# OBSERVE-ONLY HARD BOUNDARY"
    )

    coordinator_index = source.index(
        "coordinator.process_event(",
        boundary_index,
    )

    assert (
        boundary_index
        < coordinator_index
    )


def test_script_unarmed_boundary_uses_continue() -> None:
    """Observe-only mode must bypass all later mutating execution code."""

    source = script_source()

    boundary_index = source.index(
        "# OBSERVE-ONLY HARD BOUNDARY"
    )

    decision_index = source.index(
        "decision = (",
        boundary_index,
    )

    boundary_text = source[
        boundary_index:
        decision_index
    ]

    assert (
        "if not armed:"
        in boundary_text
    )

    assert (
        "continue"
        in boundary_text
    )


def test_script_observe_mode_says_coordinator_not_called() -> None:
    """Audit output must state lifecycle mutator was not called."""

    source = script_source()

    assert (
        "TradeCoordinator was NOT called."
        in source
    )


def test_script_observe_mode_says_lifecycle_not_mutated() -> None:
    """Audit output must explicitly state lifecycle remains untouched."""

    source = script_source()

    assert (
        "Durable lifecycle was NOT "
        in source
    )

    assert (
        "mutated."
        in source
    )


def test_script_observe_mode_says_risk_not_called() -> None:
    """Observe mode must stop before RiskManager."""

    source = script_source()

    assert (
        "RiskManager was NOT called."
        in source
    )


def test_script_observe_mode_says_ib_submit_not_called() -> None:
    """Observe mode must explicitly block broker submission."""

    source = script_source()

    assert (
        "IBExecutionClient.submit was "
        in source
    )

    assert (
        "NOT called."
        in source
    )


def test_script_uses_risk_manager_in_armed_path() -> None:
    """Armed path must use central RiskManager."""

    source = script_source()

    assert (
        "risk_manager.evaluate("
        in source
    )


def test_script_uses_ib_trading_readiness() -> None:
    """Armed path must pass IB readiness gate."""

    source = script_source()

    assert (
        "IBTradingReadiness("
        in source
    )

    assert (
        "readiness.require_ready("
        in source
    )


def test_script_uses_ib_execution_client() -> None:
    """Broker submission must use proven execution client."""

    source = script_source()

    assert (
        "IBExecutionClient("
        in source
    )

    assert (
        "execution_client.submit("
        in source
    )


def test_script_uses_market_day_transmit_order() -> None:
    """Continuous paper orders should use proven order configuration."""

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


def test_script_targets_paper_port_not_live_port() -> None:
    """Continuous runner must not use TWS live port."""

    source = script_source()

    assert (
        "IB_PORT = 7497"
        in source
    )

    assert (
        "IB_PORT = 7496"
        not in source
    )


def test_script_has_one_contract_risk_limits() -> None:
    """Continuous runner must retain one-unit exposure limits."""

    source = script_source()

    assert (
        "MAX_ORDER_QUANTITY = 1"
        in source
    )

    assert (
        "MAX_ABSOLUTE_POSITION = 1"
        in source
    )


def test_script_requires_reconciliation_before_submission() -> None:
    """Broker/lifecycle reconciliation must occur in live path."""

    source = script_source()

    assert (
        "reconcile_broker_and_lifecycle("
        in source
    )

    assert (
        "require_execution_state_clear("
        in source
    )


def test_script_fails_closed_on_unresolved_execution() -> None:
    """Execution uncertainty must explicitly block trading."""

    source = script_source()

    assert (
        "unresolved execution state"
        in source
    )


def test_script_verifies_post_fill_reconciliation() -> None:
    """Every filled order must be reconciled against fresh TWS state."""

    source = script_source()

    assert (
        "PAPER ORDER FILLED AND "
        in source
    )

    assert (
        "reconciled_position"
        in source
    )


def test_script_does_not_embed_api_key() -> None:
    """No Eagle credential should be hard-coded."""

    source = script_source()

    assert (
        "BTS_EAGLE_API_KEY"
        in source
    )

    assert "Bearer " not in source
    assert "SUPER-SECRET" not in source