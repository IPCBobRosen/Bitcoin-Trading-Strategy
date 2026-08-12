"""Offline tests for the IB paper disconnect/recovery harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_position_transport import IBPositionTransport
from app.ib_trading_readiness import (
    IBReadinessFailure,
    IBTradingReadiness,
)
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls

from scripts.test_ib_paper_disconnect_recovery import (
    CLIENT_ID,
    HOST,
    PORT,
    IBDisconnectRecoveryResult,
    print_result,
    require_execution_blocked_while_disconnected,
    require_flat_account,
)


def create_success_result() -> IBDisconnectRecoveryResult:
    """Create one fully successful recovery result."""

    return IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )


def complete_empty_snapshot(
    broker_client: IBBrokerClient,
) -> None:
    """Complete an empty broker snapshot through production transport."""

    transport = IBPositionTransport(
        broker_client
    )

    transport.begin_snapshot()

    transport.position_end()


def create_ready_components():
    """Create an offline fully ready BTS state."""

    broker_client = IBBrokerClient()

    complete_empty_snapshot(
        broker_client
    )

    trading_controls = TradingControls()

    trading_controls.resume()

    kill_switch = KillSwitch()

    app = IBApiPositionApp(
        broker_client,
        kill_switch=kill_switch,
    )

    app.nextValidId(
        3
    )

    readiness = IBTradingReadiness(
        api_ready=app.api_ready,
        order_id_allocator=(
            app.order_id_allocator
        ),
        broker_client=broker_client,
        trading_controls=trading_controls,
        kill_switch=kill_switch,
    )

    return (
        app,
        broker_client,
        trading_controls,
        kill_switch,
        readiness,
    )


def test_host_is_localhost() -> None:
    """Recovery harness should connect locally."""

    assert HOST == "127.0.0.1"


def test_port_is_7497() -> None:
    """Harness should use paper TWS port."""

    assert PORT == 7497


def test_client_id_is_one() -> None:
    """Harness should use BTS paper client ID."""

    assert CLIENT_ID == 1


def test_success_result_is_successful() -> None:
    """Complete healthy lifecycle should report success."""

    assert (
        create_success_result().successful
        is True
    )


def test_missing_initial_readiness_fails_result() -> None:
    """Initial BTS state must be ready."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=False,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_nonflat_initial_account_fails_result() -> None:
    """Recovery test requires a flat starting account."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=1,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_socket_must_be_disconnected() -> None:
    """Disconnect stage must actually close socket."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=False,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_api_must_not_remain_ready_after_disconnect() -> None:
    """Stale handshake state must invalidate success."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=True,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_trading_must_not_remain_ready_disconnected() -> None:
    """Disconnected BTS must fail readiness."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=True,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_execution_must_be_blocked_disconnected() -> None:
    """Readiness gate must reject execution while offline."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=False,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_reconnect_must_restore_api_readiness() -> None:
    """Successful recovery requires new handshake."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=False,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_reconnect_requires_next_valid_id() -> None:
    """Reconnect must receive a valid IB order ID."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=None,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_reconnect_account_must_still_be_flat() -> None:
    """Unexpected position after reconnect invalidates recovery."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=1,
        reconnect_readiness=True,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_reconnect_must_restore_trading_readiness() -> None:
    """Recovery isn't complete until readiness returns."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=False,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_kill_switch_invalidates_success() -> None:
    """Unexpected emergency state should fail recovery test."""

    result = IBDisconnectRecoveryResult(
        initial_api_ready=True,
        initial_next_valid_order_id=3,
        initial_position_count=0,
        initial_readiness=True,
        disconnected_socket=True,
        disconnected_api_ready=False,
        disconnected_readiness=False,
        execution_blocked_while_disconnected=True,
        reconnect_api_ready=True,
        reconnect_next_valid_order_id=3,
        reconnect_position_count=0,
        reconnect_readiness=True,
        kill_switch_active=True,
    )

    assert result.successful is False


def test_result_is_immutable() -> None:
    """Recovery result must be immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.reconnect_readiness = False  # type: ignore[misc]


def test_completed_empty_snapshot_is_flat() -> None:
    """A completed empty broker snapshot should pass."""

    broker_client = IBBrokerClient()

    complete_empty_snapshot(
        broker_client
    )

    assert (
        require_flat_account(
            broker_client
        )
        == 0
    )


def test_incomplete_snapshot_cannot_be_treated_as_flat() -> None:
    """Unknown position state must not be interpreted as zero."""

    broker_client = IBBrokerClient()

    with pytest.raises(
        RuntimeError,
        match="snapshot is not complete",
    ):
        require_flat_account(
            broker_client
        )


def test_invalid_broker_client_is_rejected() -> None:
    """Flat-account helper requires production broker client."""

    with pytest.raises(
        TypeError,
        match="'broker_client'",
    ):
        require_flat_account(
            object()  # type: ignore[arg-type]
        )


def test_initial_ready_state_passes_offline() -> None:
    """Prepared BTS state should satisfy readiness."""

    (
        _,
        _,
        _,
        _,
        readiness,
    ) = create_ready_components()

    result = readiness.require_ready(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is True


def test_api_reset_revokes_readiness_offline() -> None:
    """Resetting handshake state should immediately block execution."""

    (
        app,
        _,
        _,
        _,
        readiness,
    ) = create_ready_components()

    app.api_ready.reset()

    result = (
        require_execution_blocked_while_disconnected(
            readiness
        )
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.API_NOT_READY
        in result.failures
    )


def test_disconnect_block_helper_requires_readiness_type() -> None:
    """Helper should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'readiness'",
    ):
        require_execution_blocked_while_disconnected(
            object()  # type: ignore[arg-type]
        )


def test_deliberate_api_reset_does_not_trip_kill_switch() -> None:
    """Normal local disconnect should not become an emergency."""

    (
        app,
        _,
        _,
        kill_switch,
        readiness,
    ) = create_ready_components()

    app.api_ready.reset()

    require_execution_blocked_while_disconnected(
        readiness
    )

    assert kill_switch.active is False


def test_order_id_allocator_history_survives_api_reset() -> None:
    """Disconnect must not erase safe IB order-ID history."""

    (
        app,
        _,
        _,
        _,
        _,
    ) = create_ready_components()

    assert (
        app.order_id_allocator.next_order_id
        == 3
    )

    app.api_ready.reset()

    assert (
        app.order_id_allocator.initialized
        is True
    )

    assert (
        app.order_id_allocator.next_order_id
        == 3
    )


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful recovery should print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output

    assert "Execution blocked:" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_script_does_not_import_execution_client() -> None:
    """Recovery harness must contain no execution coordinator."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_disconnect_recovery.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.ib_execution_client import"
        not in source
    )


def test_script_contains_no_place_order_call() -> None:
    """Recovery harness must never submit a broker order."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_disconnect_recovery.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    forbidden_call = (
        "place"
        + "Order("
    )

    assert forbidden_call not in source