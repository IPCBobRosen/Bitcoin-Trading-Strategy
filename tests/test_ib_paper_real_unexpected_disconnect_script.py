"""Offline tests for the real unexpected-disconnect harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.execution_ledger import ExecutionLedger
from app.ib_broker_client import IBBrokerClient
from app.ib_error_handler import IBErrorSeverity
from app.kill_switch import KillSwitch

from scripts.test_ib_paper_real_unexpected_disconnect import (
    CLIENT_ID,
    HOST,
    PORT,
    RESET_CONFIRMATION,
    IBRealUnexpectedDisconnectResult,
    ObservingIBApiPositionApp,
    confirm_operator_reset,
    print_result,
)


def create_success_result() -> IBRealUnexpectedDisconnectResult:
    """Create one fully successful real recovery result."""

    return IBRealUnexpectedDisconnectResult(
        initially_ready=True,
        initial_position_count=0,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        readiness_after_loss=False,
        execution_blocked_after_loss=True,
        saw_restore_1101_or_1102=True,
        kill_switch_after_restore=True,
        readiness_after_restore=False,
        execution_blocked_after_restore=True,
        post_restore_position_count=0,
        operator_reset_confirmed=True,
        kill_switch_after_reset=False,
        final_readiness=True,
    )


def create_observing_app(
    tmp_path,
):
    """Create an offline observing IB app."""

    broker_client = IBBrokerClient()

    ledger = ExecutionLedger(
        tmp_path
        / "ledger.db"
    )

    kill_switch = KillSwitch()

    app = ObservingIBApiPositionApp(
        broker_client,
        execution_ledger=ledger,
        kill_switch=kill_switch,
    )

    return (
        app,
        broker_client,
        ledger,
        kill_switch,
    )


def test_host_is_localhost() -> None:
    """Real harness should use local TWS."""

    assert HOST == "127.0.0.1"


def test_port_is_paper_port() -> None:
    """Real harness should use TWS paper port."""

    assert PORT == 7497


def test_client_id_is_one() -> None:
    """Harness should retain BTS client ID."""

    assert CLIENT_ID == 1


def test_reset_confirmation_text() -> None:
    """Manual reset phrase should be explicit."""

    assert RESET_CONFIRMATION == "RESET"


def test_success_result_is_successful() -> None:
    """Complete emergency lifecycle should pass."""

    assert (
        create_success_result().successful
        is True
    )


def test_missing_1100_fails_result() -> None:
    """No observed loss message means test is invalid."""

    result = create_success_result()

    changed = IBRealUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        initial_position_count=result.initial_position_count,
        saw_error_1100=False,
        kill_switch_after_loss=result.kill_switch_after_loss,
        readiness_after_loss=result.readiness_after_loss,
        execution_blocked_after_loss=(
            result.execution_blocked_after_loss
        ),
        saw_restore_1101_or_1102=(
            result.saw_restore_1101_or_1102
        ),
        kill_switch_after_restore=(
            result.kill_switch_after_restore
        ),
        readiness_after_restore=(
            result.readiness_after_restore
        ),
        execution_blocked_after_restore=(
            result.execution_blocked_after_restore
        ),
        post_restore_position_count=(
            result.post_restore_position_count
        ),
        operator_reset_confirmed=(
            result.operator_reset_confirmed
        ),
        kill_switch_after_reset=(
            result.kill_switch_after_reset
        ),
        final_readiness=result.final_readiness,
    )

    assert changed.successful is False


def test_missing_restore_fails_result() -> None:
    """Test must observe real 1101 or 1102."""

    result = create_success_result()

    changed = IBRealUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        initial_position_count=0,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        readiness_after_loss=False,
        execution_blocked_after_loss=True,
        saw_restore_1101_or_1102=False,
        kill_switch_after_restore=True,
        readiness_after_restore=False,
        execution_blocked_after_restore=True,
        post_restore_position_count=0,
        operator_reset_confirmed=True,
        kill_switch_after_reset=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_restore_cannot_clear_kill_switch() -> None:
    """Automatic reconnect may not authorize trading."""

    result = create_success_result()

    changed = IBRealUnexpectedDisconnectResult(
        initially_ready=True,
        initial_position_count=0,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        readiness_after_loss=False,
        execution_blocked_after_loss=True,
        saw_restore_1101_or_1102=True,
        kill_switch_after_restore=False,
        readiness_after_restore=False,
        execution_blocked_after_restore=True,
        post_restore_position_count=0,
        operator_reset_confirmed=True,
        kill_switch_after_reset=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_post_restore_account_must_be_flat() -> None:
    """Unexpected exposure must invalidate recovery."""

    result = create_success_result()

    changed = IBRealUnexpectedDisconnectResult(
        initially_ready=True,
        initial_position_count=0,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        readiness_after_loss=False,
        execution_blocked_after_loss=True,
        saw_restore_1101_or_1102=True,
        kill_switch_after_restore=True,
        readiness_after_restore=False,
        execution_blocked_after_restore=True,
        post_restore_position_count=1,
        operator_reset_confirmed=True,
        kill_switch_after_reset=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_operator_confirmation_is_required() -> None:
    """Recovery cannot silently reset emergency state."""

    result = create_success_result()

    changed = IBRealUnexpectedDisconnectResult(
        initially_ready=True,
        initial_position_count=0,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        readiness_after_loss=False,
        execution_blocked_after_loss=True,
        saw_restore_1101_or_1102=True,
        kill_switch_after_restore=True,
        readiness_after_restore=False,
        execution_blocked_after_restore=True,
        post_restore_position_count=0,
        operator_reset_confirmed=False,
        kill_switch_after_reset=False,
        final_readiness=True,
    )

    assert changed.successful is False


def test_result_is_immutable() -> None:
    """Live recovery result must not mutate."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_readiness = False  # type: ignore[misc]


def test_observer_records_1100(
    tmp_path,
) -> None:
    """Observing app should retain real loss code."""

    (
        app,
        _,
        _,
        kill_switch,
    ) = create_observing_app(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    assert (
        app.has_seen_error_code(
            1100
        )
        is True
    )

    assert kill_switch.active is True

    assert (
        IBErrorSeverity.CONNECTION_LOST
        in app.observed_error_severities
    )


@pytest.mark.parametrize(
    "restore_code",
    [
        1101,
        1102,
    ],
)
def test_observer_records_restore_codes(
    tmp_path,
    restore_code: int,
) -> None:
    """Observer should recognize either restoration code."""

    (
        app,
        _,
        _,
        _,
    ) = create_observing_app(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=restore_code,
        errorString="Connectivity restored.",
    )

    assert (
        app.has_seen_connection_restore()
        is True
    )

    assert (
        restore_code
        in app.observed_error_codes
    )


def test_restore_does_not_clear_observer_kill_switch(
    tmp_path,
) -> None:
    """Real restoration callback must leave emergency active."""

    (
        app,
        _,
        _,
        kill_switch,
    ) = create_observing_app(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=1102,
        errorString="Connectivity restored.",
    )

    assert kill_switch.active is True


@pytest.mark.parametrize(
    "response",
    [
        "RESET",
        "reset",
        " Reset ",
    ],
)
def test_operator_reset_accepts_exact_phrase(
    response: str,
) -> None:
    """Explicit RESET phrase should authorize reset."""

    assert (
        confirm_operator_reset(
            input_function=lambda _: response
        )
        is True
    )


@pytest.mark.parametrize(
    "response",
    [
        "",
        "YES",
        "R",
        "RESET NOW",
        "continue",
    ],
)
def test_operator_reset_rejects_other_phrases(
    response: str,
) -> None:
    """Anything other than RESET must fail closed."""

    assert (
        confirm_operator_reset(
            input_function=lambda _: response
        )
        is False
    )


def test_operator_reset_requires_callable() -> None:
    """Input dependency must be callable."""

    with pytest.raises(
        TypeError,
        match="'input_function'",
    ):
        confirm_operator_reset(
            input_function=123  # type: ignore[arg-type]
        )


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful live result should print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "Saw real IB 1100:" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_script_has_no_execution_client_import() -> None:
    """Live outage harness must contain no order executor."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_real_unexpected_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.ib_execution_client import"
        not in source
    )


def test_script_contains_no_place_order_call() -> None:
    """Live outage harness must never place an order."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_real_unexpected_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    forbidden_call = (
        "place"
        + "Order("
    )

    assert forbidden_call not in source