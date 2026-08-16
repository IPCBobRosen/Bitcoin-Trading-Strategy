"""Offline tests for keeping an IB position after RESET."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.duplicate_order_guard import DuplicateOrderGuard
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionStatus,
)
from app.ib_execution_client import IBExecutionClient
from app.ib_order_factory import IBOrderFactory

from scripts.test_ib_paper_keep_position_after_reset import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    QUANTITY,
    RESET_CONFIRMATION,
    IBKeepPositionAfterResetResult,
    attempt_duplicate_entry,
    build_entry_request,
    confirm_reset,
    print_result,
)


def create_request() -> TradeRequest:
    """Create deterministic BUY_TO_OPEN request."""

    return TradeRequest(
        event_id="keep-position-event-001",
        signal_id="keep-position-signal-001",
        timestamp=datetime(
            2026,
            8,
            12,
            17,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )


def create_success_result() -> IBKeepPositionAfterResetResult:
    """Create fully successful keep-position result."""

    return IBKeepPositionAfterResetResult(
        event_id="keep-position-event-001",
        broker_order_id=20,
        entry_status=ExecutionStatus.FILLED,
        position_before_outage=1,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        execution_blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        execution_blocked_after_restore=True,
        reconciled_position=1,
        entry_status_after_restore=ExecutionStatus.FILLED,
        operator_reset_confirmed=True,
        readiness_after_reset=True,
        position_after_reset=1,
        duplicate_rejected=True,
        position_after_duplicate_attempt=1,
        final_kill_switch_active=False,
    )


def create_execution_client(
    tmp_path,
):
    """Create execution client with mocked broker submission."""

    ledger = ExecutionLedger(
        tmp_path
        / "ledger.db"
    )

    guard = DuplicateOrderGuard()

    place_order = Mock()

    factory = IBOrderFactory(
        exchange="CME",
        currency="USD",
        trading_class="MBT",
        order_type="MKT",
        time_in_force="DAY",
        transmit=True,
    )

    client = IBExecutionClient(
        order_factory=factory,
        duplicate_guard=guard,
        execution_ledger=ledger,
        place_order_function=place_order,
    )

    return (
        ledger,
        guard,
        place_order,
        client,
    )


def test_quantity_is_exactly_one() -> None:
    """Keep-position test may enter only one MBT."""

    assert QUANTITY == 1


def test_contract_is_august_2026() -> None:
    """Harness must use verified August expiry."""

    assert CONTRACT_MONTH == "20260828"


def test_arming_argument_is_explicit() -> None:
    """Live harness requires deliberate arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-keep-position-after-reset"
    )


def test_reset_phrase_is_reset() -> None:
    """Recovery must require explicit RESET."""

    assert RESET_CONFIRMATION == "RESET"


def test_build_entry_is_buy_to_open() -> None:
    """Entry request must be STAGING BUY_TO_OPEN 1 MBT."""

    request = build_entry_request(
        event_id="event-001"
    )

    assert request.environment is Environment.STAGING
    assert request.intent is TradeIntent.BUY_TO_OPEN
    assert request.symbol == "MBT"
    assert request.quantity == 1


def test_empty_event_id_is_rejected() -> None:
    """Event ID is required."""

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        build_entry_request(
            event_id=" "
        )


def test_success_result_is_successful() -> None:
    """Correct recovery lifecycle should pass."""

    assert (
        create_success_result().successful
        is True
    )


def test_position_after_reset_must_remain_long_one() -> None:
    """RESET itself must not flatten the broker position."""

    result = create_success_result()

    changed = IBKeepPositionAfterResetResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        entry_status=ExecutionStatus.FILLED,
        position_before_outage=1,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        execution_blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        execution_blocked_after_restore=True,
        reconciled_position=1,
        entry_status_after_restore=ExecutionStatus.FILLED,
        operator_reset_confirmed=True,
        readiness_after_reset=True,
        position_after_reset=0,
        duplicate_rejected=True,
        position_after_duplicate_attempt=1,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


def test_duplicate_must_be_rejected() -> None:
    """Success requires duplicate Eagle event rejection."""

    result = create_success_result()

    changed = IBKeepPositionAfterResetResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        entry_status=ExecutionStatus.FILLED,
        position_before_outage=1,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        execution_blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        execution_blocked_after_restore=True,
        reconciled_position=1,
        entry_status_after_restore=ExecutionStatus.FILLED,
        operator_reset_confirmed=True,
        readiness_after_reset=True,
        position_after_reset=1,
        duplicate_rejected=False,
        position_after_duplicate_attempt=1,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


def test_duplicate_attempt_must_not_change_position() -> None:
    """Duplicate rejection must leave broker exposure unchanged."""

    result = create_success_result()

    changed = IBKeepPositionAfterResetResult(
        event_id=result.event_id,
        broker_order_id=result.broker_order_id,
        entry_status=ExecutionStatus.FILLED,
        position_before_outage=1,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        execution_blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        execution_blocked_after_restore=True,
        reconciled_position=1,
        entry_status_after_restore=ExecutionStatus.FILLED,
        operator_reset_confirmed=True,
        readiness_after_reset=True,
        position_after_reset=1,
        duplicate_rejected=True,
        position_after_duplicate_attempt=2,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


@pytest.mark.parametrize(
    "response",
    [
        "RESET",
        "reset",
        " Reset ",
    ],
)
def test_reset_accepts_reset(
    response: str,
) -> None:
    """RESET should authorize recovery."""

    assert (
        confirm_reset(
            lambda _: response
        )
        is True
    )


@pytest.mark.parametrize(
    "response",
    [
        "",
        "YES",
        "CONTINUE",
        "FLATTEN",
        "RESET NOW",
    ],
)
def test_reset_rejects_other_text(
    response: str,
) -> None:
    """Other responses must fail closed."""

    assert (
        confirm_reset(
            lambda _: response
        )
        is False
    )


def test_reset_requires_callable() -> None:
    """Input dependency must be callable."""

    with pytest.raises(
        TypeError,
        match="'input_function'",
    ):
        confirm_reset(
            1  # type: ignore[arg-type]
        )


def test_duplicate_event_is_rejected_before_second_broker_call(
    tmp_path,
) -> None:
    """Durable ledger must stop duplicate before placeOrder."""

    (
        ledger,
        _,
        place_order,
        client,
    ) = create_execution_client(
        tmp_path
    )

    request = create_request()

    client.submit(
        request,
        contract_month="20260828",
        broker_order_id=20,
    )

    assert place_order.call_count == 1

    duplicate_rejected = (
        attempt_duplicate_entry(
            execution_client=client,
            trade_request=request,
            contract_month="20260828",
            broker_order_id=21,
        )
    )

    assert duplicate_rejected is True
    assert place_order.call_count == 1

    record = ledger.get(
        request.event_id
    )

    assert record is not None
    assert record.broker_order_id == 20


def test_duplicate_does_not_replace_original_order_id(
    tmp_path,
) -> None:
    """Duplicate candidate ID must never replace durable original."""

    (
        ledger,
        _,
        _,
        client,
    ) = create_execution_client(
        tmp_path
    )

    request = create_request()

    client.submit(
        request,
        contract_month="20260828",
        broker_order_id=30,
    )

    attempt_duplicate_entry(
        execution_client=client,
        trade_request=request,
        contract_month="20260828",
        broker_order_id=31,
    )

    record = ledger.get(
        request.event_id
    )

    assert record is not None
    assert record.broker_order_id == 30


def test_result_is_immutable() -> None:
    """Result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.position_after_reset = 0  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful result should report preserved position."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "preserved the reconciled +1 MBT position" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_script_has_only_one_submit_for_original_entry() -> None:
    """Original entry should have only one normal submission."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_keep_position_after_reset.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    normal_submit = (
        "execution_client.submit(\n"
        "            entry_request,"
    )

    assert source.count(
        normal_submit
    ) == 1


def test_script_contains_no_sell_to_close() -> None:
    """This harness must intentionally leave position open."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_keep_position_after_reset.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "TradeIntent.SELL_TO_CLOSE" not in source


def test_script_contains_no_sell_to_close_submission() -> None:
    """RESET harness must contain no automatic close-order path."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_keep_position_after_reset.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "TradeIntent.SELL_TO_CLOSE" not in source

    assert (
        "close_request"
        not in source
    )