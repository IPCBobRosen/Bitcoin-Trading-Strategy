"""Offline tests for the IB open-position disconnect harness."""

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
from app.ib_order_factory import IBOrderFactory

from scripts.test_ib_paper_open_position_disconnect import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    QUANTITY,
    RESET_CONFIRMATION,
    IBOpenPositionDisconnectResult,
    build_trade_request,
    confirm_reset,
    print_result,
)


def create_success_result() -> IBOpenPositionDisconnectResult:
    """Create one completely successful outage result."""

    return IBOpenPositionDisconnectResult(
        entry_event_id="entry-001",
        entry_order_id=10,
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
        close_event_id="close-001",
        close_order_id=11,
        close_status=ExecutionStatus.FILLED,
        final_position=0,
        final_kill_switch_active=False,
    )


def test_quantity_is_exactly_one() -> None:
    """Harness may trade only one MBT."""

    assert QUANTITY == 1


def test_contract_is_august_2026() -> None:
    """Harness must use verified August contract."""

    assert CONTRACT_MONTH == "20260828"


def test_arming_argument_is_explicit() -> None:
    """Live test requires deliberate arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-open-position-disconnect"
    )


def test_reset_phrase_is_explicit() -> None:
    """Emergency recovery phrase should be RESET."""

    assert RESET_CONFIRMATION == "RESET"


def test_build_entry_request() -> None:
    """Entry must be STAGING BUY_TO_OPEN 1 MBT."""

    request = build_trade_request(
        event_id="entry-001",
        signal_id="signal-001",
        intent=TradeIntent.BUY_TO_OPEN,
    )

    assert request.environment is Environment.STAGING
    assert request.intent is TradeIntent.BUY_TO_OPEN
    assert request.symbol == "MBT"
    assert request.quantity == 1


def test_build_close_request() -> None:
    """Close must be STAGING SELL_TO_CLOSE 1 MBT."""

    request = build_trade_request(
        event_id="close-001",
        signal_id="signal-001",
        intent=TradeIntent.SELL_TO_CLOSE,
    )

    assert request.environment is Environment.STAGING
    assert request.intent is TradeIntent.SELL_TO_CLOSE
    assert request.symbol == "MBT"
    assert request.quantity == 1


@pytest.mark.parametrize(
    "intent",
    [
        TradeIntent.SELL_TO_OPEN,
        TradeIntent.BUY_TO_CLOSE,
    ],
)
def test_other_intents_are_rejected(
    intent: TradeIntent,
) -> None:
    """Harness should permit only its entry and close intents."""

    with pytest.raises(
        ValueError,
        match="BUY_TO_OPEN or SELL_TO_CLOSE",
    ):
        build_trade_request(
            event_id="event-001",
            signal_id="signal-001",
            intent=intent,
        )


def test_invalid_intent_type_is_rejected() -> None:
    """Intent must use TradeIntent enum."""

    with pytest.raises(
        TypeError,
        match="'intent'",
    ):
        build_trade_request(
            event_id="event-001",
            signal_id="signal-001",
            intent="BUY_TO_OPEN",  # type: ignore[arg-type]
        )


def test_empty_event_id_is_rejected() -> None:
    """Event ID is mandatory."""

    with pytest.raises(
        ValueError,
        match="'event_id'",
    ):
        build_trade_request(
            event_id=" ",
            signal_id="signal-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )


def test_empty_signal_id_is_rejected() -> None:
    """Signal ID is mandatory."""

    with pytest.raises(
        ValueError,
        match="'signal_id'",
    ):
        build_trade_request(
            event_id="event-001",
            signal_id=" ",
            intent=TradeIntent.BUY_TO_OPEN,
        )


def test_market_factory_builds_entry() -> None:
    """Production factory should build controlled market BUY."""

    request = build_trade_request(
        event_id="entry-001",
        signal_id="signal-001",
        intent=TradeIntent.BUY_TO_OPEN,
    )

    factory = IBOrderFactory(
        exchange="CME",
        currency="USD",
        trading_class="MBT",
        order_type="MKT",
        time_in_force="DAY",
        transmit=True,
    )

    package = factory.create(
        request,
        contract_month=CONTRACT_MONTH,
    )

    assert package.order.action == "BUY"
    assert package.order.totalQuantity == 1
    assert package.order.orderType == "MKT"
    assert package.order.transmit is True


def test_market_factory_builds_close() -> None:
    """Production factory should build controlled market SELL."""

    request = build_trade_request(
        event_id="close-001",
        signal_id="signal-001",
        intent=TradeIntent.SELL_TO_CLOSE,
    )

    factory = IBOrderFactory(
        exchange="CME",
        currency="USD",
        trading_class="MBT",
        order_type="MKT",
        time_in_force="DAY",
        transmit=True,
    )

    package = factory.create(
        request,
        contract_month=CONTRACT_MONTH,
    )

    assert package.order.action == "SELL"
    assert package.order.totalQuantity == 1
    assert package.order.orderType == "MKT"
    assert package.order.transmit is True


def test_success_result_is_successful() -> None:
    """Complete safe outage lifecycle should pass."""

    assert (
        create_success_result().successful
        is True
    )


def test_entry_must_be_filled() -> None:
    """Unfilled entry invalidates test success."""

    result = create_success_result()

    changed = IBOpenPositionDisconnectResult(
        entry_event_id=result.entry_event_id,
        entry_order_id=result.entry_order_id,
        entry_status=ExecutionStatus.ACKNOWLEDGED,
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
        close_event_id=result.close_event_id,
        close_order_id=result.close_order_id,
        close_status=ExecutionStatus.FILLED,
        final_position=0,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


def test_position_must_survive_outage() -> None:
    """Recovery must reconcile the original +1 position."""

    result = create_success_result()

    changed = IBOpenPositionDisconnectResult(
        entry_event_id=result.entry_event_id,
        entry_order_id=result.entry_order_id,
        entry_status=ExecutionStatus.FILLED,
        position_before_outage=1,
        saw_error_1100=True,
        kill_switch_after_loss=True,
        execution_blocked_after_loss=True,
        saw_restore=True,
        kill_switch_after_restore=True,
        execution_blocked_after_restore=True,
        reconciled_position=0,
        entry_status_after_restore=ExecutionStatus.FILLED,
        operator_reset_confirmed=True,
        readiness_after_reset=True,
        close_event_id=result.close_event_id,
        close_order_id=result.close_order_id,
        close_status=ExecutionStatus.FILLED,
        final_position=0,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


def test_entry_order_id_cannot_be_reused_for_close() -> None:
    """Close requires a new broker order ID."""

    result = create_success_result()

    changed = IBOpenPositionDisconnectResult(
        entry_event_id=result.entry_event_id,
        entry_order_id=10,
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
        close_event_id=result.close_event_id,
        close_order_id=10,
        close_status=ExecutionStatus.FILLED,
        final_position=0,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


def test_close_must_fill() -> None:
    """Successful recovery requires confirmed close fill."""

    result = create_success_result()

    changed = IBOpenPositionDisconnectResult(
        entry_event_id=result.entry_event_id,
        entry_order_id=10,
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
        close_event_id=result.close_event_id,
        close_order_id=11,
        close_status=ExecutionStatus.CANCELLED,
        final_position=0,
        final_kill_switch_active=False,
    )

    assert changed.successful is False


def test_final_position_must_be_flat() -> None:
    """Filled close is insufficient unless broker confirms flat."""

    result = create_success_result()

    changed = IBOpenPositionDisconnectResult(
        entry_event_id=result.entry_event_id,
        entry_order_id=10,
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
        close_event_id=result.close_event_id,
        close_order_id=11,
        close_status=ExecutionStatus.FILLED,
        final_position=1,
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
def test_reset_confirmation_accepts_reset(
    response: str,
) -> None:
    """RESET should explicitly authorize recovery."""

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
        "R",
    ],
)
def test_reset_confirmation_rejects_other_text(
    response: str,
) -> None:
    """Other responses should fail closed."""

    assert (
        confirm_reset(
            lambda _: response
        )
        is False
    )


def test_result_is_immutable() -> None:
    """Outage result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.final_position = 1  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful result should print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "without duplicate exposure" in output


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_script_contains_exactly_two_submit_calls() -> None:
    """Harness should submit one entry and one close only."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_open_position_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        source.count(
            "execution_client.submit("
        )
        == 2
    )


def test_script_does_not_resubmit_entry_after_outage() -> None:
    """Entry request should be submitted to IB exactly once."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_open_position_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    entry_submit = (
        "execution_client.submit(\n"
        "            entry_request,"
    )

    assert source.count(
        entry_submit
    ) == 1