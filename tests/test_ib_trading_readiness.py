"""Tests for the BTS Interactive Brokers trading-readiness gate."""

from unittest.mock import patch

import pytest

from app.ib_api_ready import IBApiReady
from app.ib_broker_client import IBBrokerClient
from app.ib_order_id_allocator import IBOrderIdAllocator
from app.ib_trading_readiness import (
    IBReadinessFailure,
    IBReadinessStatus,
    IBTradingReadiness,
    IBTradingReadinessSnapshot,
)
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls
from app.ib_position_transport import IBPositionTransport


def create_components():
    """Create the standard BTS readiness dependencies."""

    api_ready = IBApiReady()

    allocator = IBOrderIdAllocator()

    broker_client = IBBrokerClient()

    trading_controls = TradingControls()

    kill_switch = KillSwitch()

    readiness = IBTradingReadiness(
        api_ready=api_ready,
        order_id_allocator=allocator,
        broker_client=broker_client,
        trading_controls=trading_controls,
        kill_switch=kill_switch,
    )

    return (
        readiness,
        api_ready,
        allocator,
        broker_client,
        trading_controls,
        kill_switch,
    )


def complete_position_snapshot(
    broker_client: IBBrokerClient,
) -> None:
    """Complete a broker position snapshot through the real transport."""

    transport = IBPositionTransport(
        broker_client
    )

    transport.begin_snapshot()

    transport.position_end()


def make_ready():
    """Create a fully ready BTS IB environment."""

    (
        readiness,
        api_ready,
        allocator,
        broker_client,
        trading_controls,
        kill_switch,
    ) = create_components()

    api_ready.record_next_valid_id(
        100
    )

    allocator.initialize(
        100
    )

    complete_position_snapshot(
        broker_client
    )

    trading_controls.resume()

    return (
        readiness,
        api_ready,
        allocator,
        broker_client,
        trading_controls,
        kill_switch,
    )


def test_readiness_retains_dependencies() -> None:
    """Readiness gate should retain supplied safety components."""

    (
        readiness,
        api_ready,
        allocator,
        broker_client,
        trading_controls,
        kill_switch,
    ) = create_components()

    assert readiness.api_ready is api_ready

    assert (
        readiness.order_id_allocator
        is allocator
    )

    assert (
        readiness.broker_client
        is broker_client
    )

    assert (
        readiness.trading_controls
        is trading_controls
    )

    assert (
        readiness.kill_switch
        is kill_switch
    )


def test_new_environment_is_not_ready() -> None:
    """Fresh BTS state must fail closed."""

    readiness, *_ = create_components()

    result = readiness.evaluate(
        positions_reconciled=False,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        result.status
        is IBReadinessStatus.NOT_READY
    )


def test_new_environment_reports_trading_paused() -> None:
    """Trading controls start paused and must block readiness."""

    readiness, *_ = create_components()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert (
        IBReadinessFailure.TRADING_PAUSED
        in result.failures
    )


def test_api_not_ready_blocks_trading() -> None:
    """Missing IB handshake must block trading."""

    (
        readiness,
        _,
        allocator,
        broker_client,
        trading_controls,
        _,
    ) = create_components()

    allocator.initialize(
        100
    )

    complete_position_snapshot(
        broker_client
    )

    trading_controls.resume()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.API_NOT_READY
        in result.failures
    )


def test_uninitialized_order_id_allocator_blocks_trading() -> None:
    """BTS may not trade without safe IB order IDs."""

    (
        readiness,
        api_ready,
        _,
        broker_client,
        trading_controls,
        _,
    ) = create_components()

    api_ready.record_next_valid_id(
        100
    )

    complete_position_snapshot(
        broker_client
    )

    trading_controls.resume()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.ORDER_ID_NOT_READY
        in result.failures
    )


def test_incomplete_position_snapshot_blocks_trading() -> None:
    """IB position snapshot must complete before trading."""

    (
        readiness,
        api_ready,
        allocator,
        _,
        trading_controls,
        _,
    ) = create_components()

    api_ready.record_next_valid_id(
        100
    )

    allocator.initialize(
        100
    )

    trading_controls.resume()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.POSITION_SNAPSHOT_INCOMPLETE
        in result.failures
    )


def test_position_mismatch_blocks_trading() -> None:
    """Unreconciled position state must fail closed."""

    readiness, *_ = make_ready()

    result = readiness.evaluate(
        positions_reconciled=False,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.POSITIONS_NOT_RECONCILED
        in result.failures
    )


def test_execution_uncertainty_blocks_trading() -> None:
    """Unresolved broker execution state must block new orders."""

    readiness, *_ = make_ready()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=False,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.EXECUTION_UNCERTAINTY
        in result.failures
    )


def test_kill_switch_blocks_trading() -> None:
    """Emergency kill switch must override otherwise ready state."""

    (
        readiness,
        _,
        _,
        _,
        _,
        kill_switch,
    ) = make_ready()

    kill_switch.activate(
        "Emergency test."
    )

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.KILL_SWITCH_ACTIVE
        in result.failures
    )


def test_all_conditions_satisfied_is_ready() -> None:
    """Every required condition should produce READY."""

    readiness, *_ = make_ready()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is True

    assert (
        result.status
        is IBReadinessStatus.READY
    )

    assert result.failures == ()

    assert (
        result.reason
        == "All IB trading-readiness conditions "
        "are satisfied."
    )


def test_ready_snapshot_reports_complete_state() -> None:
    """Ready snapshot should expose every successful condition."""

    readiness, *_ = make_ready()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.trading_paused is False
    assert result.kill_switch_active is False
    assert result.api_ready is True
    assert result.order_id_ready is True

    assert (
        result.position_snapshot_complete
        is True
    )

    assert result.positions_reconciled is True
    assert result.execution_state_clear is True


def test_pause_after_readiness_blocks_again() -> None:
    """Runtime pause should immediately revoke readiness."""

    (
        readiness,
        _,
        _,
        _,
        trading_controls,
        _,
    ) = make_ready()

    first = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert first.ready is True

    trading_controls.pause()

    second = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert second.ready is False

    assert (
        IBReadinessFailure.TRADING_PAUSED
        in second.failures
    )


def test_kill_switch_activation_revokes_readiness() -> None:
    """Kill switch should immediately make BTS not ready."""

    (
        readiness,
        _,
        _,
        _,
        _,
        kill_switch,
    ) = make_ready()

    assert (
        readiness.evaluate(
            positions_reconciled=True,
            execution_state_clear=True,
        ).ready
        is True
    )

    kill_switch.activate(
        "IB connectivity lost."
    )

    assert (
        readiness.evaluate(
            positions_reconciled=True,
            execution_state_clear=True,
        ).ready
        is False
    )


def test_api_reset_revokes_readiness() -> None:
    """IB disconnect should immediately revoke handshake readiness."""

    (
        readiness,
        api_ready,
        _,
        _,
        _,
        _,
    ) = make_ready()

    api_ready.reset()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.API_NOT_READY
        in result.failures
    )


def test_multiple_failures_are_reported_together() -> None:
    """Gate should expose every blocking condition."""

    readiness, *_ = create_components()

    result = readiness.evaluate(
        positions_reconciled=False,
        execution_state_clear=False,
    )

    assert (
        IBReadinessFailure.TRADING_PAUSED
        in result.failures
    )

    assert (
        IBReadinessFailure.API_NOT_READY
        in result.failures
    )

    assert (
        IBReadinessFailure.ORDER_ID_NOT_READY
        in result.failures
    )

    assert (
        IBReadinessFailure.POSITION_SNAPSHOT_INCOMPLETE
        in result.failures
    )

    assert (
        IBReadinessFailure.POSITIONS_NOT_RECONCILED
        in result.failures
    )

    assert (
        IBReadinessFailure.EXECUTION_UNCERTAINTY
        in result.failures
    )


def test_failure_reason_lists_blocking_conditions() -> None:
    """Human-readable reason should explain why trading is blocked."""

    readiness, *_ = create_components()

    result = readiness.evaluate(
        positions_reconciled=False,
        execution_state_clear=False,
    )

    assert "TradingPaused" in result.reason
    assert "ApiNotReady" in result.reason

    assert (
        "PositionSnapshotIncomplete"
        in result.reason
    )

    assert (
        "PositionsNotReconciled"
        in result.reason
    )


def test_require_ready_returns_snapshot_when_ready() -> None:
    """Ready environment should pass mandatory gate."""

    readiness, *_ = make_ready()

    result = readiness.require_ready(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert isinstance(
        result,
        IBTradingReadinessSnapshot,
    )

    assert result.ready is True


def test_require_ready_raises_when_not_ready() -> None:
    """Blocked environment must prevent broker submission."""

    readiness, *_ = create_components()

    with pytest.raises(
        RuntimeError,
        match="IB trading is blocked by",
    ):
        readiness.require_ready(
            positions_reconciled=False,
            execution_state_clear=False,
        )


def test_require_ready_error_contains_specific_failure() -> None:
    """Execution caller should receive useful blocking reason."""

    readiness, *_ = make_ready()

    with pytest.raises(
        RuntimeError,
        match="PositionsNotReconciled",
    ):
        readiness.require_ready(
            positions_reconciled=False,
            execution_state_clear=True,
        )


def test_snapshot_is_immutable() -> None:
    """Readiness snapshots must not be mutable."""

    readiness, *_ = make_ready()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    with pytest.raises(
        AttributeError,
    ):
        result.ready = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_value",
    [
        1,
        0,
        "True",
        None,
    ],
)
def test_invalid_positions_reconciled_is_rejected(
    invalid_value,
) -> None:
    """Position reconciliation input must be explicit bool."""

    readiness, *_ = create_components()

    with pytest.raises(
        TypeError,
        match="'positions_reconciled' must be a bool",
    ):
        readiness.evaluate(
            positions_reconciled=invalid_value,
            execution_state_clear=True,
        )


@pytest.mark.parametrize(
    "invalid_value",
    [
        1,
        0,
        "True",
        None,
    ],
)
def test_invalid_execution_state_clear_is_rejected(
    invalid_value,
) -> None:
    """Execution-clear input must be explicit bool."""

    readiness, *_ = create_components()

    with pytest.raises(
        TypeError,
        match="'execution_state_clear' must be a bool",
    ):
        readiness.evaluate(
            positions_reconciled=True,
            execution_state_clear=invalid_value,
        )


def test_invalid_api_ready_is_rejected() -> None:
    """Constructor requires IBApiReady."""

    with pytest.raises(
        TypeError,
        match="'api_ready'",
    ):
        IBTradingReadiness(
            api_ready=object(),  # type: ignore[arg-type]
            order_id_allocator=IBOrderIdAllocator(),
            broker_client=IBBrokerClient(),
            trading_controls=TradingControls(),
            kill_switch=KillSwitch(),
        )


def test_invalid_allocator_is_rejected() -> None:
    """Constructor requires IBOrderIdAllocator."""

    with pytest.raises(
        TypeError,
        match="'order_id_allocator'",
    ):
        IBTradingReadiness(
            api_ready=IBApiReady(),
            order_id_allocator=object(),  # type: ignore[arg-type]
            broker_client=IBBrokerClient(),
            trading_controls=TradingControls(),
            kill_switch=KillSwitch(),
        )


def test_invalid_broker_client_is_rejected() -> None:
    """Constructor requires IBBrokerClient."""

    with pytest.raises(
        TypeError,
        match="'broker_client'",
    ):
        IBTradingReadiness(
            api_ready=IBApiReady(),
            order_id_allocator=IBOrderIdAllocator(),
            broker_client=object(),  # type: ignore[arg-type]
            trading_controls=TradingControls(),
            kill_switch=KillSwitch(),
        )


def test_invalid_trading_controls_is_rejected() -> None:
    """Constructor requires TradingControls."""

    with pytest.raises(
        TypeError,
        match="'trading_controls'",
    ):
        IBTradingReadiness(
            api_ready=IBApiReady(),
            order_id_allocator=IBOrderIdAllocator(),
            broker_client=IBBrokerClient(),
            trading_controls=object(),  # type: ignore[arg-type]
            kill_switch=KillSwitch(),
        )


def test_invalid_kill_switch_is_rejected() -> None:
    """Constructor requires KillSwitch."""

    with pytest.raises(
        TypeError,
        match="'kill_switch'",
    ):
        IBTradingReadiness(
            api_ready=IBApiReady(),
            order_id_allocator=IBOrderIdAllocator(),
            broker_client=IBBrokerClient(),
            trading_controls=TradingControls(),
            kill_switch=object(),  # type: ignore[arg-type]
        )