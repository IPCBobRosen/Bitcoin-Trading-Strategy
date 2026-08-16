"""Tests for the IB already-positioned entry-guard harness."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

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
from app.kill_switch import KillSwitch
from app.daily_loss_guard import DailyLossGuard
from app.risk_manager import RiskManager
from app.trading_controls import TradingControls

from scripts.test_ib_paper_already_positioned_entry_guard import (
    ARMING_ARGUMENT,
    CONTRACT_MONTH,
    IBAlreadyPositionedEntryGuardResult,
    ForbiddenBrokerSubmission,
    attempt_duplicate_replay,
    build_new_entry_request,
    load_original_filled_entry,
    reconstruct_original_request,
    print_result,
)


def create_request(
    *,
    event_id: str = "original-event-001",
) -> TradeRequest:
    """Create deterministic BUY_TO_OPEN request."""

    return TradeRequest(
        event_id=event_id,
        signal_id="original-signal-001",
        timestamp=datetime(
            2026,
            8,
            16,
            22,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )


def create_filled_ledger(
    tmp_path,
) -> ExecutionLedger:
    """Create durable original filled BUY event."""

    ledger = ExecutionLedger(
        tmp_path
        / "filled.db"
    )

    request = create_request()

    ledger.reserve(
        request
    )

    ledger.mark_submitted(
        request.event_id,
        broker_order_id=8,
    )

    ledger.mark_filled(
        request.event_id
    )

    return ledger


def create_success_result(
) -> IBAlreadyPositionedEntryGuardResult:
    """Create successful positioned-entry result."""

    return IBAlreadyPositionedEntryGuardResult(
        source_event_id="original-event-001",
        source_broker_order_id=8,
        initial_position=1,
        duplicate_rejected=True,
        broker_calls_after_duplicate=0,
        position_after_duplicate=1,
        new_event_id="new-event-001",
        new_entry_risk_rejected=True,
        projected_position=2,
        broker_calls_after_new_entry=0,
        position_after_new_entry=1,
    )


def test_arming_argument_is_explicit() -> None:
    """Live test must require deliberate arming."""

    assert (
        ARMING_ARGUMENT
        == "--confirm-positioned-entry-guard"
    )


def test_contract_is_august_2026() -> None:
    """Harness should retain verified August contract."""

    assert CONTRACT_MONTH == "20260828"


def test_success_result_is_successful() -> None:
    """Correct safety lifecycle should pass."""

    assert (
        create_success_result().successful
        is True
    )


def test_duplicate_must_be_rejected() -> None:
    """Duplicate acceptance invalidates success."""

    result = create_success_result()

    changed = IBAlreadyPositionedEntryGuardResult(
        source_event_id=result.source_event_id,
        source_broker_order_id=8,
        initial_position=1,
        duplicate_rejected=False,
        broker_calls_after_duplicate=0,
        position_after_duplicate=1,
        new_event_id=result.new_event_id,
        new_entry_risk_rejected=True,
        projected_position=2,
        broker_calls_after_new_entry=0,
        position_after_new_entry=1,
    )

    assert changed.successful is False


def test_duplicate_must_not_reach_broker() -> None:
    """Any broker call after duplicate invalidates safety."""

    result = create_success_result()

    changed = IBAlreadyPositionedEntryGuardResult(
        source_event_id=result.source_event_id,
        source_broker_order_id=8,
        initial_position=1,
        duplicate_rejected=True,
        broker_calls_after_duplicate=1,
        position_after_duplicate=1,
        new_event_id=result.new_event_id,
        new_entry_risk_rejected=True,
        projected_position=2,
        broker_calls_after_new_entry=0,
        position_after_new_entry=1,
    )

    assert changed.successful is False


def test_new_entry_must_be_rejected() -> None:
    """A brand-new +2-producing entry must fail."""

    result = create_success_result()

    changed = IBAlreadyPositionedEntryGuardResult(
        source_event_id=result.source_event_id,
        source_broker_order_id=8,
        initial_position=1,
        duplicate_rejected=True,
        broker_calls_after_duplicate=0,
        position_after_duplicate=1,
        new_event_id=result.new_event_id,
        new_entry_risk_rejected=False,
        projected_position=2,
        broker_calls_after_new_entry=0,
        position_after_new_entry=1,
    )

    assert changed.successful is False


def test_projected_position_must_be_two() -> None:
    """Safety test must actually exercise +1 to +2."""

    result = create_success_result()

    changed = IBAlreadyPositionedEntryGuardResult(
        source_event_id=result.source_event_id,
        source_broker_order_id=8,
        initial_position=1,
        duplicate_rejected=True,
        broker_calls_after_duplicate=0,
        position_after_duplicate=1,
        new_event_id=result.new_event_id,
        new_entry_risk_rejected=True,
        projected_position=1,
        broker_calls_after_new_entry=0,
        position_after_new_entry=1,
    )

    assert changed.successful is False


def test_final_position_must_remain_one() -> None:
    """No guard test may alter live broker exposure."""

    result = create_success_result()

    changed = IBAlreadyPositionedEntryGuardResult(
        source_event_id=result.source_event_id,
        source_broker_order_id=8,
        initial_position=1,
        duplicate_rejected=True,
        broker_calls_after_duplicate=0,
        position_after_duplicate=1,
        new_event_id=result.new_event_id,
        new_entry_risk_rejected=True,
        projected_position=2,
        broker_calls_after_new_entry=0,
        position_after_new_entry=2,
    )

    assert changed.successful is False


def test_load_original_filled_entry(
    tmp_path,
) -> None:
    """Harness should retrieve durable filled BUY event."""

    ledger = create_filled_ledger(
        tmp_path
    )

    record = load_original_filled_entry(
        ledger
    )

    assert record.event_id == "original-event-001"
    assert record.broker_order_id == 8
    assert record.status is ExecutionStatus.FILLED
    assert record.intent == "BUY_TO_OPEN"
    assert record.quantity == 1


def test_load_original_requires_one_match(
    tmp_path,
) -> None:
    """Missing source event must fail closed."""

    ledger = ExecutionLedger(
        tmp_path
        / "empty.db"
    )

    with pytest.raises(
        RuntimeError,
        match="exactly one",
    ):
        load_original_filled_entry(
            ledger
        )


def test_reconstruct_preserves_event_identity(
    tmp_path,
) -> None:
    """Duplicate replay should use original event identity."""

    ledger = create_filled_ledger(
        tmp_path
    )

    record = load_original_filled_entry(
        ledger
    )

    request = reconstruct_original_request(
        record
    )

    assert request.event_id == record.event_id
    assert request.signal_id == record.signal_id
    assert request.intent is TradeIntent.BUY_TO_OPEN
    assert request.quantity == 1
    assert request.symbol == "MBT"


def test_new_entry_has_new_event_id() -> None:
    """New risk test should use a fresh event."""

    request = build_new_entry_request()

    assert request.event_id.startswith(
        "paper-positioned-new-entry-"
    )

    assert request.intent is TradeIntent.BUY_TO_OPEN
    assert request.environment is Environment.STAGING
    assert request.quantity == 1


def test_forbidden_broker_starts_unused() -> None:
    """Broker sentinel should begin with zero calls."""

    sentinel = ForbiddenBrokerSubmission()

    assert sentinel.call_count == 0


def test_forbidden_broker_raises_if_called() -> None:
    """Any accidental broker submission must fail immediately."""

    sentinel = ForbiddenBrokerSubmission()

    with pytest.raises(
        RuntimeError,
        match="Safety violation",
    ):
        sentinel(
            1,
            object(),
            object(),
        )

    assert sentinel.call_count == 1


def test_duplicate_replay_is_rejected_before_broker(
    tmp_path,
) -> None:
    """Durable filled event must never reach broker twice."""

    ledger = create_filled_ledger(
        tmp_path
    )

    record = load_original_filled_entry(
        ledger
    )

    request = reconstruct_original_request(
        record
    )

    sentinel = ForbiddenBrokerSubmission()

    client = IBExecutionClient(
        order_factory=IBOrderFactory(
            exchange="CME",
            currency="USD",
            trading_class="MBT",
            order_type="MKT",
            time_in_force="DAY",
            transmit=True,
        ),
        duplicate_guard=DuplicateOrderGuard(),
        execution_ledger=ledger,
        place_order_function=sentinel,
    )

    rejected = attempt_duplicate_replay(
        execution_client=client,
        original_request=request,
        candidate_order_id=9,
    )

    assert rejected is True
    assert sentinel.call_count == 0

    durable = ledger.get(
        record.event_id
    )

    assert durable is not None
    assert durable.broker_order_id == 8
    assert durable.status is ExecutionStatus.FILLED


def test_risk_manager_rejects_new_buy_when_long_one() -> None:
    """New BUY at +1 must violate max absolute position."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )

    controls.resume()

    kill_switch = KillSwitch()

    risk_manager = RiskManager(
        controls,
        kill_switch,
        DailyLossGuard(
            Decimal("1000")
        ),
        allowed_symbols=(
            "MBT",
        ),
        max_order_quantity=1,
        max_absolute_position=1,
    )

    decision = risk_manager.evaluate(
        build_new_entry_request(),
        current_position=1,
    )

    assert decision.approved is False
    assert decision.projected_position == 2


def test_risk_manager_allows_same_entry_when_flat() -> None:
    """Test proves rejection specifically depends on existing +1."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=Decimal("500"),
    )

    controls.resume()

    risk_manager = RiskManager(
        controls,
        KillSwitch(),
        DailyLossGuard(
            Decimal("1000")
        ),
        allowed_symbols=(
            "MBT",
        ),
        max_order_quantity=1,
        max_absolute_position=1,
    )

    decision = risk_manager.evaluate(
        build_new_entry_request(),
        current_position=0,
    )

    assert decision.approved is True
    assert decision.projected_position == 1


def test_result_is_immutable() -> None:
    """Safety result must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.position_after_new_entry = 2  # type: ignore[misc]


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful guard should clearly print PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output
    assert "prevented +2 MBT exposure" in output
    assert "remains LONG +1 MBT" in output


def test_print_result_requires_correct_type() -> None:
    """Printer must reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )