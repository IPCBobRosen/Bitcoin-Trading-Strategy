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
    DEFAULT_EVENT_DATABASE,
    DEFAULT_EXECUTION_LEDGER,
    DEFAULT_LIFECYCLE_DATABASE,
    DEFAULT_MAX_MESSAGES,
    EAGLE_API_KEY_ENVIRONMENT_VARIABLE,
    EAGLE_URI,
    IB_CLIENT_ID,
    IB_HOST,
    IB_PORT,
    MAX_CONFIGURABLE_QUANTITY,
    RECOVERY_ARGUMENT,
    validate_runtime_execution_config,
    SYMBOL,
    DurableOpenSignal,
    ReservedExitRecoveryDecision,
    evaluate_reserved_exit_recovery,
    execution_state_clear,
    expected_ib_action,
    expected_position_after_trade,
    find_reserved_exit,
    get_mbt_position,
    get_relevant_btc_eagle_open_positions,
    get_missed_eagle_signal_ids,
    load_durable_open_signals,
    require_eagle_hello_reconciled,
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
    """LIVE Eagle credential must remain outside source."""

    assert (
        EAGLE_API_KEY_ENVIRONMENT_VARIABLE
        == "BTS_EAGLE_LIVE_API_KEY"
    )


def test_runner_targets_tws_paper_port() -> None:
    """Continuous runner must use TWS paper endpoint."""

    assert IB_HOST == "127.0.0.1"
    assert IB_PORT == 7497
    assert IB_CLIENT_ID == 1


def test_runner_trades_mbt_only() -> None:
    """Continuous runner must remain restricted to MBT."""

    assert SYMBOL == "MBT"


def test_max_configurable_quantity_is_ten() -> None:
    """Operator-selected MBT quantity must retain an independent hard ceiling."""

    assert MAX_CONFIGURABLE_QUANTITY == 10


def test_runtime_config_accepts_one_contract() -> None:
    """Current one-lot workflow must remain valid."""

    config = validate_runtime_execution_config(
        contract_month="20260925",
        local_symbol="MBTU6",
        quantity=1,
    )

    assert config.contract_month == "20260925"
    assert config.local_symbol == "MBTU6"
    assert config.quantity == 1


def test_runtime_config_accepts_five_contracts() -> None:
    """Operator may explicitly choose a larger approved position."""

    config = validate_runtime_execution_config(
        contract_month="20260925",
        local_symbol="MBTU6",
        quantity=5,
    )

    assert config.quantity == 5


def test_runtime_config_accepts_quantity_at_hard_ceiling() -> None:
    """Ten MBT is the initial maximum configurable size."""

    config = validate_runtime_execution_config(
        contract_month="20260925",
        local_symbol="MBTU6",
        quantity=10,
    )

    assert config.quantity == 10


def test_runtime_config_rejects_quantity_above_hard_ceiling() -> None:
    """Quantity 11 must fail even if the operator requests it."""

    with pytest.raises(
        ValueError,
        match="10",
    ):
        validate_runtime_execution_config(
            contract_month="20260925",
            local_symbol="MBTU6",
            quantity=11,
        )


@pytest.mark.parametrize(
    "quantity",
    [0, -1, True],
)
def test_runtime_config_rejects_invalid_quantity(quantity) -> None:
    """Runtime MBT quantity must be an integer from 1 through 10."""

    with pytest.raises(
        ValueError,
        match="quantity",
    ):
        validate_runtime_execution_config(
            contract_month="20260925",
            local_symbol="MBTU6",
            quantity=quantity,
        )


@pytest.mark.parametrize(
    "contract_month",
    ["", "2026-09-25", "ABC", "2026092"],
)
def test_runtime_config_rejects_invalid_contract_month(
    contract_month: str,
) -> None:
    """Execution contract must use an IB-compatible expiry identifier."""

    with pytest.raises(
        ValueError,
        match="contract",
    ):
        validate_runtime_execution_config(
            contract_month=contract_month,
            local_symbol="MBTU6",
            quantity=1,
        )


def test_runtime_config_normalizes_local_symbol() -> None:
    """Expected TWS local symbol should be normalized for reconciliation."""

    config = validate_runtime_execution_config(
        contract_month="20260925",
        local_symbol="  mbtu6  ",
        quantity=1,
    )

    assert config.local_symbol == "MBTU6"


@pytest.mark.parametrize(
    "local_symbol",
    ["", "   ", 123],
)
def test_runtime_config_rejects_invalid_local_symbol(
    local_symbol,
) -> None:
    """Missing or malformed TWS contract identity must fail closed."""

    with pytest.raises(
        ValueError,
        match="local_symbol",
    ):
        validate_runtime_execution_config(
            contract_month="20260925",
            local_symbol=local_symbol,
            quantity=1,
        )


def test_default_message_limit_is_continuous() -> None:
    """Zero should represent continuous operation."""

    assert DEFAULT_MAX_MESSAGES == 0


def test_exact_arming_argument() -> None:
    """Continuous broker execution must require explicit arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-continuous-paper"
    )


def test_durable_paths_use_isolated_live_state() -> None:
    """LIVE Eagle runner must use isolated durable databases."""

    assert (
        DEFAULT_EVENT_DATABASE.name
        == "real_eagle_live_events.db"
    )

    assert (
        DEFAULT_LIFECYCLE_DATABASE.name
        == "real_eagle_live_signals.db"
    )

    assert (
        DEFAULT_EXECUTION_LEDGER.name
        == "real_eagle_live_execution.db"
    )


def test_get_mbt_position_flat_snapshot() -> None:
    """Completed empty broker snapshot should report zero."""

    broker = build_completed_broker()

    assert (
        get_mbt_position(
            broker,
            expected_local_symbol="MBTQ6",
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
            broker,
            expected_local_symbol="MBTQ6",
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
            broker,
            expected_local_symbol="MBTQ6",
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
            broker,
            expected_local_symbol="MBTQ6",
        )


def test_require_no_other_positions_accepts_flat() -> None:
    """Flat broker state should pass."""

    broker = build_completed_broker()

    require_no_other_broker_positions(
        broker,
        expected_local_symbol="MBTQ6",
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
        broker,
        expected_local_symbol="MBTQ6",
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
            broker,
            expected_local_symbol="MBTQ6",
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
            expected_local_symbol="MBTQ6",
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
            expected_local_symbol="MBTQ6",
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
            expected_local_symbol="MBTQ6",
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
            expected_local_symbol="MBTQ6",
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
            expected_local_symbol="MBTQ6",
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
        expected_quantity=1,
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
        expected_quantity=1,
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
            expected_quantity=1,
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
        expected_quantity=1,
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
        expected_quantity=1,
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
            expected_quantity=1,
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
            expected_quantity=1,
        )


def test_validate_trade_request_accepts_runtime_quantity() -> None:
    """Trade validation should accept the operator-approved runtime quantity."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
        quantity=5,
    )

    validate_trade_request_against_position(
        trade_request=request,
        broker_position=0,
        open_signals=(),
        expected_quantity=5,
    )


def test_validate_trade_request_rejects_wrong_runtime_quantity() -> None:
    """Trade quantity must exactly match the approved runtime quantity."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
        quantity=2,
    )

    with pytest.raises(
        RuntimeError,
        match="quantity",
    ):
        validate_trade_request_against_position(
            trade_request=request,
            broker_position=0,
            open_signals=(),
            expected_quantity=5,
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



def test_script_has_observe_only_boundary_before_coordinator() -> None:
    """Unarmed live mode must stop before TradeCoordinator preparation."""

    source = script_source()

    boundary_index = source.index(
        "# OBSERVE-ONLY HARD BOUNDARY"
    )

    prepare_index = source.index(
        "coordinator.prepare_event(",
        boundary_index,
    )

    assert boundary_index < prepare_index


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


def test_script_uses_runtime_quantity_for_controls_and_risk() -> None:
    """One operator-selected quantity must drive every position-size guard."""

    source = script_source()

    assert "quantity=execution_config.quantity" in source
    assert (
        "max_order_quantity=execution_config.quantity"
        in source
    )
    assert (
        "max_absolute_position=execution_config.quantity"
        in source
    )


def test_script_has_independent_quantity_ceiling() -> None:
    """Runtime risk size must remain bounded independently of operator input."""

    source = script_source()

    assert "MAX_CONFIGURABLE_QUANTITY = 10" in source


def test_script_does_not_hardcode_august_contract_for_submission() -> None:
    """Broker submissions must use the approved runtime contract."""

    source = script_source()

    assert 'CONTRACT_MONTH = "20260828"' not in source
    assert (
        "contract_month=execution_config.contract_month"
        in source
    )


def test_all_submission_paths_use_runtime_contract_month() -> None:
    """Recovery, reserved-close, and normal paths must share one contract."""

    source = script_source()

    assert (
        source.count(
            "contract_month=execution_config.contract_month"
        )
        == 3
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
        "BTS_EAGLE_LIVE_API_KEY"
        in source
    )

    assert "Bearer " not in source
    assert "SUPER-SECRET" not in source

def test_armed_live_path_runs_risk_before_lifecycle_mutation() -> None:
    """Risk approval must occur before durable lifecycle commit."""

    source = script_source()

    armed_path_index = source.index(
        "# Armed path begins here."
    )

    prepare_index = source.index(
        "coordinator.prepare_event(",
        armed_path_index,
    )

    risk_index = source.index(
        "risk_manager.evaluate(",
        armed_path_index,
    )

    commit_index = source.index(
        "coordinator.commit_request(",
        armed_path_index,
    )

    assert (
        prepare_index
        < risk_index
        < commit_index
    )

def test_already_positioned_opening_signal_is_nonfatal_skip() -> None:
    """Second Eagle entry at max position must not kill continuous trader."""

    source = script_source()

    expected_message = (
        "Opening signal skipped because BTS/broker "
        "is already positioned."
    )

    live_index = source.index(
        "# LIVE lifecycle event."
    )

    positioned_guard_index = source.index(
        "if broker_position != 0 or open_signals:",
        live_index,
    )

    skip_message_index = source.index(
        expected_message,
        positioned_guard_index,
    )

    continue_index = source.index(
        "continue",
        skip_message_index,
    )

    next_runtime_error_index = source.find(
        "raise RuntimeError(",
        positioned_guard_index,
    )

    assert continue_index > skip_message_index

    assert (
        next_runtime_error_index == -1
        or continue_index < next_runtime_error_index
    )

def test_skipped_second_entry_is_durably_consumed_before_continue() -> None:
    """Skipped max-position entry must advance durable Eagle processing."""

    source = script_source()

    live_index = source.index(
        "# LIVE lifecycle event."
    )

    positioned_guard_index = source.index(
        "if broker_position != 0 or open_signals:",
        live_index,
    )

    event_process_index = source.index(
        "event_processor.process(message)",
        positioned_guard_index,
    )

    skip_message_index = source.index(
        "Opening signal skipped because BTS/broker is already positioned.",
        positioned_guard_index,
    )

    continue_index = source.index(
        "continue",
        skip_message_index,
    )

    commit_index = source.index(
        "coordinator.commit_request(",
        positioned_guard_index,
    )

    assert (
        event_process_index
        < skip_message_index
        < continue_index
        < commit_index
    )

def test_exit_is_reserved_before_readiness_and_lifecycle_commit() -> None:
    """Exit obligation must survive broker-readiness failure."""

    source = script_source()

    durability_boundary_index = source.index(
        "# EXIT-OBLIGATION DURABILITY BOUNDARY"
    )

    reserve_index = source.index(
        "execution_client.reserve_execution(",
        durability_boundary_index,
    )

    readiness_index = source.index(
        "readiness.require_ready(",
        reserve_index,
    )

    submit_reserved_index = source.index(
        "execution_client.submit_reserved(",
        readiness_index,
    )

    fill_wait_index = source.index(
        "wait_for_execution_resolution(",
        submit_reserved_index,
    )

    commit_index = source.index(
        "coordinator.commit_request(",
        fill_wait_index,
    )

    assert (
        reserve_index
        < readiness_index
        < submit_reserved_index
        < fill_wait_index
        < commit_index
    )

def test_exit_obligation_is_reserved_before_broker_refresh() -> None:
    """Matching live exit must become durable before BTS touches IB."""

    source = script_source()

    durability_boundary_index = source.index(
        "# EXIT-OBLIGATION DURABILITY BOUNDARY"
    )

    event_process_index = source.index(
        "event_result = event_processor.process(",
        durability_boundary_index,
    )

    prepare_index = source.index(
        "coordinator.prepare_event(",
        event_process_index,
    )

    reserve_index = source.index(
        "execution_client.reserve_execution(",
        prepare_index,
    )

    refresh_index = source.index(
        "refresh_position_snapshot(",
        reserve_index,
    )

    assert (
        durability_boundary_index
        < event_process_index
        < prepare_index
        < reserve_index
        < refresh_index
    )


def test_exit_lifecycle_commit_occurs_only_after_confirmed_fill() -> None:
    """Close lifecycle must remain open until the broker confirms the exit fill."""

    source = script_source()

    durability_boundary_index = source.index(
        "# EXIT-OBLIGATION DURABILITY BOUNDARY"
    )

    submit_index = source.index(
        "execution_client.submit_reserved(",
        durability_boundary_index,
    )

    fill_wait_index = source.index(
        "wait_for_execution_resolution(",
        submit_index,
    )

    commit_index = source.index(
        "coordinator.commit_request(",
        durability_boundary_index,
    )

    assert (
        submit_index
        < fill_wait_index
        < commit_index
    )

def test_reserved_exit_recovery_requires_explicit_argument() -> None:
    """Pending exit recovery must require separate operator authorization."""

    assert RECOVERY_ARGUMENT == "--recover-reserved-exit"


def test_find_reserved_exit_returns_matching_close(
    tmp_path: Path,
) -> None:
    """Exactly one RESERVED closing execution should be discoverable."""

    ledger = ExecutionLedger(
        tmp_path / "execution.db"
    )

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="signal-a",
    )

    ledger.reserve(request)

    record = find_reserved_exit(ledger)

    assert record is not None
    assert record.event_id == request.event_id
    assert record.signal_id == "signal-a"
    assert record.intent is TradeIntent.SELL_TO_CLOSE
    assert record.status is ExecutionStatus.RESERVED


def test_find_reserved_exit_rejects_reserved_open(
    tmp_path: Path,
) -> None:
    """Recovery mode must never recover a missed opening order."""

    ledger = ExecutionLedger(
        tmp_path / "execution.db"
    )

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_OPEN,
        signal_id="signal-a",
    )

    ledger.reserve(request)

    with pytest.raises(
        RuntimeError,
        match="opening",
    ):
        find_reserved_exit(ledger)


def test_matching_reserved_long_exit_is_recoverable() -> None:
    """SELL_TO_CLOSE may recover only against matching +1 LONG_OPEN."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="signal-a",
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-a:entry",
    )

    decision = evaluate_reserved_exit_recovery(
        trade_request=request,
        broker_position=1,
        open_signals=(open_signal,),
        recovery_authorized=True,
        expected_quantity=1,
    )

    assert isinstance(
        decision,
        ReservedExitRecoveryDecision,
    )
    assert decision.allowed is True


def test_matching_reserved_short_exit_is_recoverable() -> None:
    """BUY_TO_CLOSE may recover only against matching -1 SHORT_OPEN."""

    request = build_trade_request(
        intent=TradeIntent.BUY_TO_CLOSE,
        signal_id="signal-a",
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-a:entry",
    )

    decision = evaluate_reserved_exit_recovery(
        trade_request=request,
        broker_position=-1,
        open_signals=(open_signal,),
        recovery_authorized=True,
        expected_quantity=1,
    )

    assert decision.allowed is True


def test_reserved_exit_recovery_rejects_flat_broker() -> None:
    """Already-flat broker must never receive another closing order."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="signal-a",
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-a:entry",
    )

    decision = evaluate_reserved_exit_recovery(
        trade_request=request,
        broker_position=0,
        open_signals=(open_signal,),
        recovery_authorized=True,
        expected_quantity=1,
    )

    assert decision.allowed is False
    assert "flat" in decision.reason.lower()


def test_reserved_exit_recovery_rejects_wrong_side() -> None:
    """A closing recovery must fail against opposite broker exposure."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="signal-a",
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-a:entry",
    )

    decision = evaluate_reserved_exit_recovery(
        trade_request=request,
        broker_position=-1,
        open_signals=(open_signal,),
        recovery_authorized=True,
        expected_quantity=1,
    )

    assert decision.allowed is False


def test_reserved_exit_recovery_rejects_wrong_signal_id() -> None:
    """Reserved exit must belong to the exact durable open signal."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="signal-b",
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-a:entry",
    )

    decision = evaluate_reserved_exit_recovery(
        trade_request=request,
        broker_position=1,
        open_signals=(open_signal,),
        recovery_authorized=True,
        expected_quantity=1,
    )

    assert decision.allowed is False


def test_reserved_exit_recovery_rejects_without_authorization() -> None:
    """Perfectly matching state still requires explicit operator approval."""

    request = build_trade_request(
        intent=TradeIntent.SELL_TO_CLOSE,
        signal_id="signal-a",
    )

    open_signal = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-a:entry",
    )

    decision = evaluate_reserved_exit_recovery(
        trade_request=request,
        broker_position=1,
        open_signals=(open_signal,),
        recovery_authorized=False,
        expected_quantity=1,
    )

    assert decision.allowed is False
    assert "authorization" in decision.reason.lower()


def test_recovery_argument_is_wired_into_argument_parser() -> None:
    """Recovery authorization must be an explicit command-line switch."""

    source = script_source()

    parser_index = source.index(
        "def parse_arguments("
    )

    recovery_argument_index = source.index(
        "RECOVERY_ARGUMENT",
        parser_index,
    )

    assert recovery_argument_index > parser_index


def test_recovery_path_is_separate_from_normal_live_event_path() -> None:
    """Startup recovery must have an explicit auditable code boundary."""

    source = script_source()

    assert (
        "# RESERVED-EXIT RECOVERY BOUNDARY"
        in source
    )


def test_recovery_checks_state_before_submit_reserved() -> None:
    """Recovery decision must be approved before any recovered broker call."""

    source = script_source()

    boundary_index = source.index(
        "# RESERVED-EXIT RECOVERY BOUNDARY"
    )

    find_index = source.index(
        "find_reserved_exit(",
        boundary_index,
    )

    evaluate_index = source.index(
        "evaluate_reserved_exit_recovery(",
        find_index,
    )

    submit_index = source.index(
        "execution_client.submit_reserved(",
        evaluate_index,
    )

    assert (
        boundary_index
        < find_index
        < evaluate_index
        < submit_index
    )


def test_recovery_submission_occurs_exactly_once_in_recovery_boundary() -> None:
    """One approved RESERVED exit must have one recovery submission site."""

    source = script_source()

    boundary_index = source.index(
        "# RESERVED-EXIT RECOVERY BOUNDARY"
    )

    live_loop_index = source.index(
        "async for message in eagle_client.listen():",
        boundary_index,
    )

    recovery_text = source[
        boundary_index:
        live_loop_index
    ]

    assert (
        recovery_text.count(
            "execution_client.submit_reserved("
        )
        == 1
    )


def test_recovery_waits_for_execution_resolution_after_submission() -> None:
    """Recovered exit must reach a known broker resolution before lifecycle close."""

    source = script_source()

    boundary_index = source.index(
        "# RESERVED-EXIT RECOVERY BOUNDARY"
    )

    submit_index = source.index(
        "execution_client.submit_reserved(",
        boundary_index,
    )

    fill_wait_index = source.index(
        "wait_for_execution_resolution(",
        submit_index,
    )

    assert submit_index < fill_wait_index


def test_recovery_commits_lifecycle_only_after_confirmed_fill() -> None:
    """Recovered exit must not close lifecycle before broker fill confirmation."""

    source = script_source()

    boundary_index = source.index(
        "# RESERVED-EXIT RECOVERY BOUNDARY"
    )

    submit_index = source.index(
        "execution_client.submit_reserved(",
        boundary_index,
    )

    fill_wait_index = source.index(
        "wait_for_execution_resolution(",
        submit_index,
    )

    commit_index = source.index(
        "coordinator.commit_request(",
        fill_wait_index,
    )

    assert (
        submit_index
        < fill_wait_index
        < commit_index
    )


def test_recovery_reconciles_fresh_broker_state_after_fill() -> None:
    """Recovered fill must be followed by a fresh broker position check."""

    source = script_source()

    boundary_index = source.index(
        "# RESERVED-EXIT RECOVERY BOUNDARY"
    )

    fill_wait_index = source.index(
        "wait_for_execution_resolution(",
        boundary_index,
    )

    refresh_index = source.index(
        "refresh_position_snapshot(",
        fill_wait_index,
    )

    reconcile_index = source.index(
        "reconcile_broker_and_lifecycle(",
        refresh_index,
    )

    assert (
        fill_wait_index
        < refresh_index
        < reconcile_index
    )

def test_eagle_hello_empty_open_snapshot_has_no_relevant_btc() -> None:
    """Empty Eagle hello snapshot should contain no relevant BTC opens."""

    relevant = get_relevant_btc_eagle_open_positions(
        ()
    )

    assert relevant == ()


def test_eagle_hello_eth_open_is_ignored_by_btc_only_runner() -> None:
    """Valid ETH open signal must not block the BTC-only BTS runner."""

    open_positions = (
        {
            "signal_id": "eth-signal-001",
            "symbol": "ETHUSDT",
            "direction": "long",
        },
    )

    relevant = get_relevant_btc_eagle_open_positions(
        open_positions
    )

    assert relevant == ()


def test_eagle_hello_btc_open_is_relevant() -> None:
    """BTCUSDT open signal must remain visible to startup safety."""

    btc_position = {
        "signal_id": "btc-signal-001",
        "symbol": "BTCUSDT",
        "direction": "long",
    }

    relevant = get_relevant_btc_eagle_open_positions(
        (btc_position,)
    )

    assert relevant == (
        btc_position,
    )


def test_eagle_hello_mixed_snapshot_returns_only_btc() -> None:
    """Mixed Eagle snapshot must filter out unsupported instruments."""

    eth_position = {
        "signal_id": "eth-signal-001",
        "symbol": "ETHUSDT",
        "direction": "long",
    }

    btc_position = {
        "signal_id": "btc-signal-001",
        "symbol": "BTCUSDT",
        "direction": "short",
    }

    relevant = get_relevant_btc_eagle_open_positions(
        (
            eth_position,
            btc_position,
        )
    )

    assert relevant == (
        btc_position,
    )


def test_eagle_hello_symbol_matching_is_normalized() -> None:
    """Startup filtering should use the same normalized symbol convention."""

    btc_position = {
        "signal_id": "btc-signal-001",
        "symbol": "  btcusdt  ",
        "direction": "long",
    }

    relevant = get_relevant_btc_eagle_open_positions(
        (btc_position,)
    )

    assert relevant == (
        btc_position,
    )


def test_eagle_hello_missing_symbol_fails_closed() -> None:
    """Ambiguous Eagle open position must never be silently ignored."""

    open_positions = (
        {
            "signal_id": "unknown-signal-001",
            "direction": "long",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="symbol",
    ):
        get_relevant_btc_eagle_open_positions(
            open_positions
        )


def test_eagle_hello_blank_symbol_fails_closed() -> None:
    """Blank Eagle symbol must fail closed during startup filtering."""

    open_positions = (
        {
            "signal_id": "unknown-signal-001",
            "symbol": "   ",
            "direction": "long",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="symbol",
    ):
        get_relevant_btc_eagle_open_positions(
            open_positions
        )


def test_eagle_hello_non_string_symbol_fails_closed() -> None:
    """Non-string Eagle symbol must fail closed."""

    open_positions = (
        {
            "signal_id": "unknown-signal-001",
            "symbol": 123,
            "direction": "long",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="symbol",
    ):
        get_relevant_btc_eagle_open_positions(
            open_positions
        )


def test_runner_does_not_gate_startup_on_raw_eagle_open_count() -> None:
    """Startup safety must evaluate relevant BTC opens, not global count."""

    source = script_source()

    assert (
        "if message.open_count != 0:"
        not in source
    )


def test_runner_filters_eagle_hello_open_positions_for_btc() -> None:
    """Runner must explicitly filter Eagle hello opens for BTCUSDT."""

    source = script_source()

    assert (
        "get_relevant_btc_eagle_open_positions("
        in source
    )

    assert (
        "message.open_positions"
        in source
    )


def test_runner_reconciles_relevant_btc_eagle_open_at_startup() -> None:
    """Known Eagle open must cross the Eagle/BTS/TWS reconciliation gate."""

    source = script_source()

    filter_index = source.index(
        "get_relevant_btc_eagle_open_positions("
    )

    reconcile_index = source.index(
        "require_eagle_hello_reconciled(",
        filter_index,
    )

    heartbeat_index = source.index(
        "elif isinstance(message, EagleHeartbeat):",
        reconcile_index,
    )

    assert (
        filter_index
        < reconcile_index
        < heartbeat_index
    )

def test_contract_month_argument_is_required() -> None:
    """Operator must explicitly identify the approved MBT expiry."""

    source = script_source()
    parser_index = source.index("def parse_arguments(")
    argument_index = source.index('"--contract-month"', parser_index)
    required_index = source.index("required=True", argument_index)

    assert parser_index < argument_index < required_index


def test_local_symbol_argument_is_required() -> None:
    """Operator must explicitly identify the expected TWS local symbol."""

    source = script_source()
    parser_index = source.index("def parse_arguments(")
    argument_index = source.index('"--local-symbol"', parser_index)
    required_index = source.index("required=True", argument_index)

    assert parser_index < argument_index < required_index


def test_quantity_argument_is_required() -> None:
    """Operator must explicitly choose MBT order quantity."""

    source = script_source()
    parser_index = source.index("def parse_arguments(")
    argument_index = source.index('"--quantity"', parser_index)
    required_index = source.index("required=True", argument_index)

    assert parser_index < argument_index < required_index


def test_main_builds_one_validated_execution_config() -> None:
    """CLI contract and quantity choices must cross one validation boundary."""

    source = script_source()
    main_index = source.index("def main(")
    config_index = source.index(
        "validate_runtime_execution_config(",
        main_index,
    )
    run_index = source.index(
        "run_continuous_paper_trader(",
        config_index,
    )

    assert main_index < config_index < run_index


def test_runner_receives_runtime_execution_config() -> None:
    """Validated configuration must be passed into continuous execution."""

    source = script_source()
    runner_index = source.index(
        "async def run_continuous_paper_trader("
    )
    parameter_index = source.index(
        "execution_config:",
        runner_index,
    )

    assert parameter_index > runner_index


def test_preflight_prints_operator_execution_configuration() -> None:
    """Operator must see exact contract and size before trading."""

    source = script_source()

    assert "APPROVED MBT EXECUTION CONFIGURATION" in source
    assert "Contract month:" in source
    assert "Expected local symbol:" in source
    assert "Order quantity:" in source
    assert "Hard quantity ceiling:" in source


def test_get_mbt_position_uses_runtime_local_symbol() -> None:
    """Position reconciliation may not depend on MBTQ6 global constant."""

    source = script_source()
    function_index = source.index("def get_mbt_position(")
    next_function_index = source.index(
        "\ndef ",
        function_index + 1,
    )
    function_source = source[
        function_index:next_function_index
    ]

    assert "expected_local_symbol" in function_source
    assert "EXPECTED_LOCAL_SYMBOL" not in function_source


def test_reserved_exit_recovery_uses_runtime_quantity() -> None:
    """Recovery authorization must match the currently approved size."""

    source = script_source()
    function_index = source.index(
        "def evaluate_reserved_exit_recovery("
    )
    next_function_index = source.index(
        "\ndef ",
        function_index + 1,
    )
    function_source = source[
        function_index:next_function_index
    ]

    assert "expected_quantity" in function_source
    assert "PAPER_QUANTITY" not in function_source


def test_eagle_hello_reconciliation_accepts_flat_everywhere() -> None:
    """Flat Eagle + flat BTS + flat broker should allow startup."""

    require_eagle_hello_reconciled(
        relevant_eagle_open_positions=(),
        broker_position=0,
        open_signals=(),
        expected_quantity=1,
    )


def test_eagle_hello_reconciliation_accepts_matching_short_restart() -> None:
    """Known Eagle short may resume when Eagle, BTS, and broker all agree."""

    eagle_open = {
        "signal_id": "signal-short-001",
        "symbol": "BTCUSDT",
        "direction": "short",
    }
    durable_open = DurableOpenSignal(
        signal_id="signal-short-001",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-short-001:entry",
    )

    require_eagle_hello_reconciled(
        relevant_eagle_open_positions=(eagle_open,),
        broker_position=-1,
        open_signals=(durable_open,),
        expected_quantity=1,
    )


def test_eagle_hello_reconciliation_accepts_matching_long_restart() -> None:
    """Known Eagle long may resume when Eagle, BTS, and broker all agree."""

    eagle_open = {
        "signal_id": "signal-long-001",
        "symbol": "BTCUSDT",
        "direction": "long",
    }
    durable_open = DurableOpenSignal(
        signal_id="signal-long-001",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="signal-long-001:entry",
    )

    require_eagle_hello_reconciled(
        relevant_eagle_open_positions=(eagle_open,),
        broker_position=5,
        open_signals=(durable_open,),
        expected_quantity=5,
    )


def test_eagle_hello_reconciliation_rejects_eagle_open_when_flat() -> None:
    """Unexpected Eagle exposure must still fail closed."""

    eagle_open = {
        "signal_id": "signal-a",
        "symbol": "BTCUSDT",
        "direction": "long",
    }

    with pytest.raises(
        RuntimeError,
        match="flat",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=(eagle_open,),
            broker_position=0,
            open_signals=(),
            expected_quantity=1,
        )


def test_eagle_hello_reconciliation_rejects_missing_eagle_open() -> None:
    """Broker/BTS exposure without matching Eagle open must fail closed."""

    durable_open = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-a:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="Eagle hello",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=(),
            broker_position=-1,
            open_signals=(durable_open,),
            expected_quantity=1,
        )


def test_eagle_hello_reconciliation_rejects_wrong_signal_id() -> None:
    """Signal identity, not just market direction, must match exactly."""

    eagle_open = {
        "signal_id": "signal-b",
        "symbol": "BTCUSDT",
        "direction": "short",
    }
    durable_open = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-a:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="signal_id",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=(eagle_open,),
            broker_position=-1,
            open_signals=(durable_open,),
            expected_quantity=1,
        )


def test_eagle_hello_reconciliation_rejects_wrong_direction() -> None:
    """Matching signal ID cannot override an Eagle/broker direction mismatch."""

    eagle_open = {
        "signal_id": "signal-a",
        "symbol": "BTCUSDT",
        "direction": "long",
    }
    durable_open = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-a:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="direction",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=(eagle_open,),
            broker_position=-1,
            open_signals=(durable_open,),
            expected_quantity=1,
        )


def test_eagle_hello_reconciliation_rejects_multiple_btc_opens() -> None:
    """Multiple relevant Eagle positions are ambiguous and must fail closed."""

    opens = (
        {
            "signal_id": "signal-a",
            "symbol": "BTCUSDT",
            "direction": "long",
        },
        {
            "signal_id": "signal-b",
            "symbol": "BTCUSDT",
            "direction": "short",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="more than one",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=opens,
            broker_position=1,
            open_signals=(
                DurableOpenSignal(
                    signal_id="signal-a",
                    state=SignalLifecycleState.LONG_OPEN,
                    last_event_id="signal-a:entry",
                ),
            ),
            expected_quantity=1,
        )


def test_eagle_hello_reconciliation_rejects_missing_signal_id() -> None:
    """Ambiguous Eagle position identity must fail closed."""

    eagle_open = {
        "symbol": "BTCUSDT",
        "direction": "short",
    }
    durable_open = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-a:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="signal_id",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=(eagle_open,),
            broker_position=-1,
            open_signals=(durable_open,),
            expected_quantity=1,
        )


def test_eagle_hello_reconciliation_rejects_invalid_direction() -> None:
    """Unsupported Eagle direction must fail closed."""

    eagle_open = {
        "signal_id": "signal-a",
        "symbol": "BTCUSDT",
        "direction": "sideways",
    }
    durable_open = DurableOpenSignal(
        signal_id="signal-a",
        state=SignalLifecycleState.SHORT_OPEN,
        last_event_id="signal-a:entry",
    )

    with pytest.raises(
        RuntimeError,
        match="unsupported direction",
    ):
        require_eagle_hello_reconciled(
            relevant_eagle_open_positions=(eagle_open,),
            broker_position=-1,
            open_signals=(durable_open,),
            expected_quantity=1,
        )


def test_eagle_hello_startup_reconciliation_does_not_submit_order() -> None:
    """Hello snapshot reconciliation must remain outside broker submission."""

    source = script_source()

    hello_index = source.index(
        "if isinstance(message, EagleHello):"
    )
    heartbeat_index = source.index(
        "elif isinstance(message, EagleHeartbeat):",
        hello_index,
    )
    hello_source = source[
        hello_index:heartbeat_index
    ]

    assert "require_eagle_hello_reconciled(" in hello_source
    assert "execution_client.submit(" not in hello_source
    assert "execution_client.submit_reserved(" not in hello_source
    assert "placeOrder(" not in hello_source


def test_missed_eagle_open_is_classified_when_bts_and_broker_flat() -> None:
    """An Eagle open with flat BTS/TWS is a missed trade, not a startup error."""

    eagle_open = {
        "signal_id": "missed-btc-001",
        "symbol": "BTCUSDT",
        "direction": "short",
    }

    missed = get_missed_eagle_signal_ids(
        relevant_eagle_open_positions=(
            eagle_open,
        ),
        broker_position=0,
        open_signals=(),
    )

    assert missed == frozenset(
        {"missed-btc-001"}
    )


def test_missed_eagle_classifier_does_not_override_real_position_reconciliation() -> None:
    """Existing BTS/broker exposure must continue through strict reconciliation."""

    eagle_open = {
        "signal_id": "btc-001",
        "symbol": "BTCUSDT",
        "direction": "long",
    }

    durable_open = DurableOpenSignal(
        signal_id="btc-001",
        state=SignalLifecycleState.LONG_OPEN,
        last_event_id="btc-001:entry",
    )

    missed = get_missed_eagle_signal_ids(
        relevant_eagle_open_positions=(
            eagle_open,
        ),
        broker_position=1,
        open_signals=(
            durable_open,
        ),
    )

    assert missed == frozenset()


def test_missed_eagle_classifier_requires_signal_id() -> None:
    """A missed trade still needs a durable identity for safe suppression."""

    eagle_open = {
        "symbol": "BTCUSDT",
        "direction": "short",
    }

    with pytest.raises(
        RuntimeError,
        match="signal_id",
    ):
        get_missed_eagle_signal_ids(
            relevant_eagle_open_positions=(
                eagle_open,
            ),
            broker_position=0,
            open_signals=(),
        )


def test_missed_eagle_classifier_rejects_duplicate_signal_ids() -> None:
    """Duplicate Eagle IDs are ambiguous and must fail closed."""

    opens = (
        {
            "signal_id": "same-id",
            "symbol": "BTCUSDT",
            "direction": "long",
        },
        {
            "signal_id": "same-id",
            "symbol": "BTCUSDT",
            "direction": "short",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="duplicate",
    ):
        get_missed_eagle_signal_ids(
            relevant_eagle_open_positions=opens,
            broker_position=0,
            open_signals=(),
        )


def test_runner_classifies_flat_eagle_open_as_missed_before_strict_reconcile() -> None:
    """Flat BTS/TWS must classify an existing Eagle open instead of failing startup."""

    source = script_source()

    hello_index = source.index(
        "if isinstance(message, EagleHello):"
    )

    heartbeat_index = source.index(
        "elif isinstance(message, EagleHeartbeat):",
        hello_index,
    )

    hello_source = source[
        hello_index:heartbeat_index
    ]

    classify_index = hello_source.index(
        "get_missed_eagle_signal_ids("
    )

    strict_index = hello_source.index(
        "require_eagle_hello_reconciled(",
        classify_index,
    )

    assert classify_index < strict_index
    assert "Existing Eagle " in hello_source
    assert "BTC positions will NOT be chased." in hello_source


def test_missed_replay_entry_cannot_reach_trade_coordinator() -> None:
    """Replay entry for a missed signal must not manufacture BTS lifecycle state."""

    source = script_source()

    replay_index = source.index(
        "# REPLAY lifecycle events"
    )

    live_index = source.index(
        "# LIVE lifecycle event.",
        replay_index,
    )

    replay_source = source[
        replay_index:live_index
    ]

    missed_index = replay_source.index(
        "message.signal_id in missed_eagle_signal_ids"
    )

    coordinator_index = replay_source.index(
        "coordinator.process_event("
    )

    assert missed_index < coordinator_index
    assert (
        "MISSED EAGLE TRADE REPLAY CONSUMED"
        in replay_source
    )


def test_missed_live_exit_is_consumed_before_adapter_and_submission() -> None:
    """Exit from a trade BTS never entered must never create a broker close."""

    source = script_source()

    live_index = source.index(
        "# LIVE lifecycle event."
    )

    adapter_index = source.index(
        "adapt_result = adapter.adapt(message)",
        live_index,
    )

    missed_index = source.index(
        "if message.signal_id in missed_eagle_signal_ids:",
        live_index,
    )

    assert missed_index < adapter_index

    missed_block = source[
        missed_index:adapter_index
    ]

    assert (
        "event_processor.process("
        in missed_block
    )
    assert (
        "missed_eagle_signal_ids.discard("
        in missed_block
    )
    assert (
        "execution_client.submit("
        not in missed_block
    )
    assert (
        "execution_client.submit_reserved("
        not in missed_block
    )


def test_missed_trade_policy_explicitly_waits_for_next_fresh_entry() -> None:
    """Operator output must explain that BTS resumes on the next new BTC entry."""

    source = script_source()

    assert "BTS will trade the next fresh BTC fund.entry " in source
    assert "normally." in source

    assert "BTS remains flat and ready for the " in source
    assert "next fresh BTC fund.entry." in source
