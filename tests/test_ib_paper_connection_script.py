"""Tests for the read-only IB paper connection harness."""

from pathlib import Path

import pytest

from scripts.test_ib_paper_connection import (
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_PORT,
    IBPaperConnectionResult,
    print_result,
    wait_until,
)


def create_success_result() -> IBPaperConnectionResult:
    """Create one successful read-only connection result."""

    return IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=True,
        next_valid_order_id=100,
        order_id_allocator_initialized=True,
        position_snapshot_complete=True,
        position_count=0,
        kill_switch_active=False,
    )


def test_default_host_is_localhost() -> None:
    """Paper harness should default to local TWS."""

    assert DEFAULT_HOST == "127.0.0.1"


def test_default_port_is_paper_tws_port() -> None:
    """Harness should use configured paper-TWS port."""

    assert DEFAULT_PORT == 7497


def test_default_client_id_is_one() -> None:
    """BTS paper harness should use client ID 1."""

    assert DEFAULT_CLIENT_ID == 1


def test_successful_result_is_successful() -> None:
    """Complete healthy state should pass."""

    result = create_success_result()

    assert result.successful is True


def test_api_not_ready_is_unsuccessful() -> None:
    """Missing API handshake must fail the harness."""

    result = IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=False,
        next_valid_order_id=100,
        order_id_allocator_initialized=True,
        position_snapshot_complete=True,
        position_count=0,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_missing_next_valid_id_is_unsuccessful() -> None:
    """Missing nextValidId must fail."""

    result = IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=True,
        next_valid_order_id=None,
        order_id_allocator_initialized=True,
        position_snapshot_complete=True,
        position_count=0,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_uninitialized_allocator_is_unsuccessful() -> None:
    """Order-ID allocator must initialize from handshake."""

    result = IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=True,
        next_valid_order_id=100,
        order_id_allocator_initialized=False,
        position_snapshot_complete=True,
        position_count=0,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_incomplete_position_snapshot_is_unsuccessful() -> None:
    """Initial broker snapshot must finish."""

    result = IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=True,
        next_valid_order_id=100,
        order_id_allocator_initialized=True,
        position_snapshot_complete=False,
        position_count=0,
        kill_switch_active=False,
    )

    assert result.successful is False


def test_active_kill_switch_is_unsuccessful() -> None:
    """Emergency state must fail connection validation."""

    result = IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=True,
        next_valid_order_id=100,
        order_id_allocator_initialized=True,
        position_snapshot_complete=True,
        position_count=0,
        kill_switch_active=True,
    )

    assert result.successful is False


def test_open_positions_do_not_by_themselves_fail_connection() -> None:
    """Read-only connection test may discover existing positions."""

    result = IBPaperConnectionResult(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        api_ready=True,
        next_valid_order_id=100,
        order_id_allocator_initialized=True,
        position_snapshot_complete=True,
        position_count=2,
        kill_switch_active=False,
    )

    assert result.successful is True


def test_result_is_immutable() -> None:
    """Connection result should not be mutable."""

    result = create_success_result()

    with pytest.raises(
        AttributeError,
    ):
        result.api_ready = False  # type: ignore[misc]


def test_wait_until_returns_immediately_when_true() -> None:
    """Already-satisfied condition should not sleep."""

    sleeps: list[float] = []

    wait_until(
        lambda: True,
        description="test condition",
        sleep_function=sleeps.append,
    )

    assert sleeps == []


def test_wait_until_polls_until_condition_becomes_true() -> None:
    """Helper should poll an asynchronous condition."""

    state = {
        "calls": 0,
    }

    def condition() -> bool:
        state["calls"] += 1

        return (
            state["calls"]
            >= 3
        )

    times = iter(
        [
            0.0,
            0.1,
            0.2,
        ]
    )

    sleeps: list[float] = []

    wait_until(
        condition,
        description="test condition",
        timeout_seconds=1.0,
        poll_interval_seconds=0.05,
        sleep_function=sleeps.append,
        monotonic_function=lambda: next(
            times
        ),
    )

    assert state["calls"] == 3
    assert sleeps == [0.05, 0.05]


def test_wait_until_times_out() -> None:
    """Unsatisfied condition should fail with useful reason."""

    times = iter(
        [
            0.0,
            0.5,
            1.0,
        ]
    )

    with pytest.raises(
        TimeoutError,
        match="IB handshake",
    ):
        wait_until(
            lambda: False,
            description="IB handshake",
            timeout_seconds=1.0,
            poll_interval_seconds=0.05,
            sleep_function=lambda _: None,
            monotonic_function=lambda: next(
                times
            ),
        )


def test_invalid_condition_is_rejected() -> None:
    """Polling condition must be callable."""

    with pytest.raises(
        TypeError,
        match="'condition'",
    ):
        wait_until(
            object(),  # type: ignore[arg-type]
            description="test",
        )


@pytest.mark.parametrize(
    "invalid_description",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_description_is_rejected(
    invalid_description,
) -> None:
    """Wait description must contain text."""

    with pytest.raises(
        ValueError,
        match="'description'",
    ):
        wait_until(
            lambda: True,
            description=invalid_description,
        )


@pytest.mark.parametrize(
    "invalid_timeout",
    [
        0,
        -1,
        True,
        "10",
        None,
    ],
)
def test_invalid_timeout_is_rejected(
    invalid_timeout,
) -> None:
    """Timeout must be a positive number."""

    with pytest.raises(
        ValueError,
        match="'timeout_seconds'",
    ):
        wait_until(
            lambda: True,
            description="test",
            timeout_seconds=invalid_timeout,
        )


@pytest.mark.parametrize(
    "invalid_poll",
    [
        0,
        -1,
        True,
        "0.05",
        None,
    ],
)
def test_invalid_poll_interval_is_rejected(
    invalid_poll,
) -> None:
    """Polling interval must be a positive number."""

    with pytest.raises(
        ValueError,
        match="'poll_interval_seconds'",
    ):
        wait_until(
            lambda: True,
            description="test",
            poll_interval_seconds=invalid_poll,
        )


def test_invalid_sleep_function_is_rejected() -> None:
    """Sleep dependency must be callable."""

    with pytest.raises(
        TypeError,
        match="'sleep_function'",
    ):
        wait_until(
            lambda: True,
            description="test",
            sleep_function=123,  # type: ignore[arg-type]
        )


def test_invalid_monotonic_function_is_rejected() -> None:
    """Clock dependency must be callable."""

    with pytest.raises(
        TypeError,
        match="'monotonic_function'",
    ):
        wait_until(
            lambda: True,
            description="test",
            monotonic_function=123,  # type: ignore[arg-type]
        )


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful connection should print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output

    assert (
        "Order submission path:    NOT PRESENT"
        in output
    )


def test_print_result_reports_connection_values(
    capsys,
) -> None:
    """Output should show connection configuration."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "127.0.0.1" in output
    assert "7497" in output
    assert "Client ID:                1" in output


def test_script_source_does_not_import_order_execution_client() -> None:
    """Read-only harness must not import BTS order executor."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_connection.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.ib_execution_client import"
        not in source
    )


def test_script_source_contains_no_broker_order_call() -> None:
    """Read-only harness must contain no broker submission invocation."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_connection.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    forbidden_call = (
        "place"
        + "Order("
    )

    assert forbidden_call not in source