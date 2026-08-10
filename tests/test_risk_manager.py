"""Tests for BTS pre-execution risk management."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.daily_loss_guard import DailyLossGuard
from app.kill_switch import KillSwitch
from app.risk_manager import (
    RiskDecisionStatus,
    RiskManager,
)
from app.trading_controls import TradingControls


def create_controls(
    *,
    paused: bool = False,
) -> TradingControls:
    """Create trading controls for risk tests."""

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    if not paused:
        controls.resume()

    return controls


def create_manager(
    *,
    paused: bool = False,
    kill_switch: KillSwitch | None = None,
    daily_loss_guard: DailyLossGuard | None = None,
    allowed_symbols: tuple[str, ...] = ("MBT",),
    max_order_quantity: int = 50,
    max_absolute_position: int = 50,
) -> RiskManager:
    """Create a configured RiskManager for tests."""

    if kill_switch is None:
        kill_switch = KillSwitch()

    if daily_loss_guard is None:
        daily_loss_guard = DailyLossGuard(
            5000
        )

    return RiskManager(
        create_controls(
            paused=paused
        ),
        kill_switch,
        daily_loss_guard,
        allowed_symbols=allowed_symbols,
        max_order_quantity=max_order_quantity,
        max_absolute_position=max_absolute_position,
    )


def create_trade_request(
    *,
    intent_value: str = "BUY_TO_OPEN",
    symbol: str = "MBT",
    quantity: int = 1,
) -> TradeRequest:
    """Create a deterministic TradeRequest for risk tests."""

    return TradeRequest(
        event_id="risk-event-001",
        signal_id="risk-signal-001",
        timestamp=datetime(
            2026,
            8,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        environment=Environment.STAGING,
        intent=TradeIntent(
            intent_value
        ),
        symbol=symbol,
        quantity=quantity,
        stop_loss_points=Decimal("500"),
    )


def trip_daily_loss(
    guard: DailyLossGuard,
) -> None:
    """Trip a daily-loss guard deterministically."""

    guard.update_realized_pnl(
        -guard.max_daily_loss
    )


def test_valid_buy_to_open_is_approved() -> None:
    """Valid new long exposure should pass risk checks."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN",
            quantity=2,
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.approved is True
    assert result.current_position == 0
    assert result.projected_position == 2


def test_valid_sell_to_open_is_approved() -> None:
    """Valid new short exposure should pass risk checks."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_OPEN",
            quantity=3,
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == -3


def test_paused_trading_rejects_buy_to_open() -> None:
    """Paused BTS must reject new long exposure."""

    manager = create_manager(
        paused=True
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN"
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.approved is False
    assert "paused" in result.reason.lower()


def test_paused_trading_rejects_sell_to_open() -> None:
    """Paused BTS must reject new short exposure."""

    manager = create_manager(
        paused=True
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_OPEN"
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "paused" in result.reason.lower()


def test_paused_trading_allows_sell_to_close() -> None:
    """Paused BTS must still permit reducing a long position."""

    manager = create_manager(
        paused=True
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=2,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 3


def test_paused_trading_allows_buy_to_close() -> None:
    """Paused BTS must still permit reducing a short position."""

    manager = create_manager(
        paused=True
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=2,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == -3


def test_active_kill_switch_rejects_buy_to_open() -> None:
    """Kill switch must block new long exposure."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Operator emergency stop."
    )

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN"
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.approved is False
    assert "kill switch" in result.reason.lower()


def test_active_kill_switch_rejects_sell_to_open() -> None:
    """Kill switch must block new short exposure."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Operator emergency stop."
    )

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_OPEN"
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "kill switch" in result.reason.lower()


def test_kill_switch_allows_sell_to_close_existing_long() -> None:
    """Kill switch must permit reducing an existing long."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Emergency flatten."
    )

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=2,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 3


def test_kill_switch_allows_buy_to_close_existing_short() -> None:
    """Kill switch must permit reducing an existing short."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Emergency flatten."
    )

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=2,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == -3


def test_kill_switch_allows_exact_long_flatten() -> None:
    """Kill switch should permit flattening an entire long."""

    kill_switch = KillSwitch()
    kill_switch.activate("Flatten long.")

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=5,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 0


def test_kill_switch_allows_exact_short_flatten() -> None:
    """Kill switch should permit flattening an entire short."""

    kill_switch = KillSwitch()
    kill_switch.activate("Flatten short.")

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=5,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 0


def test_kill_switch_rejects_long_over_close() -> None:
    """Emergency long close must not cross through flat."""

    kill_switch = KillSwitch()
    kill_switch.activate("Flatten.")

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=6,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == -1
    assert "cross through flat" in result.reason.lower()


def test_kill_switch_rejects_short_over_close() -> None:
    """Emergency short close must not cross through flat."""

    kill_switch = KillSwitch()
    kill_switch.activate("Flatten.")

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=6,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == 1
    assert "cross through flat" in result.reason.lower()


def test_kill_switch_rejection_contains_activation_reason() -> None:
    """Opening-risk rejection should preserve emergency reason."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Broker position mismatch."
    )

    manager = create_manager(
        kill_switch=kill_switch
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN"
        ),
        current_position=0,
    )

    assert "broker position mismatch" in result.reason.lower()


def test_daily_loss_rejects_buy_to_open() -> None:
    """Breached daily loss limit must block new long risk."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN"
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "daily loss limit" in result.reason.lower()


def test_daily_loss_rejects_sell_to_open() -> None:
    """Breached daily loss limit must block new short risk."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_OPEN"
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "daily loss limit" in result.reason.lower()


def test_daily_loss_allows_sell_to_close_existing_long() -> None:
    """Loss breach must permit reducing an existing long."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=2,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 3


def test_daily_loss_allows_buy_to_close_existing_short() -> None:
    """Loss breach must permit reducing an existing short."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=2,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == -3


def test_daily_loss_allows_exact_long_flatten() -> None:
    """Loss breach should permit flattening an entire long."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=5,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 0


def test_daily_loss_allows_exact_short_flatten() -> None:
    """Loss breach should permit flattening an entire short."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=5,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 0


def test_daily_loss_rejects_long_over_close() -> None:
    """Loss-state close must not reverse long into short."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=6,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == -1


def test_daily_loss_rejects_short_over_close() -> None:
    """Loss-state close must not reverse short into long."""

    guard = DailyLossGuard(
        5000
    )

    trip_daily_loss(
        guard
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=6,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == 1


def test_close_bypasses_normal_max_order_quantity() -> None:
    """Risk-reducing close may exceed normal opening-order limit."""

    manager = create_manager(
        max_order_quantity=2
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=5,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 0


def test_open_does_not_bypass_normal_max_order_quantity() -> None:
    """Opening risk remains subject to normal order limit."""

    manager = create_manager(
        max_order_quantity=2
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN",
            quantity=5,
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "maximum order quantity" in result.reason.lower()


def test_disallowed_symbol_is_rejected() -> None:
    """Symbols outside risk allowlist must be rejected."""

    manager = create_manager(
        allowed_symbols=(
            "MBT",
        )
    )

    result = manager.evaluate(
        create_trade_request(
            symbol="MES",
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "not permitted" in result.reason.lower()


def test_disallowed_symbol_close_is_rejected() -> None:
    """Emergency exit cannot use an unauthorized contract symbol."""

    manager = create_manager(
        allowed_symbols=(
            "MBT",
        )
    )

    result = manager.evaluate(
        create_trade_request(
            symbol="MES",
            intent_value="SELL_TO_CLOSE",
        ),
        current_position=1,
    )

    assert result.status is RiskDecisionStatus.REJECTED


def test_symbol_matching_is_case_insensitive() -> None:
    """Normalized symbols should match the allowlist."""

    manager = create_manager(
        allowed_symbols=(
            "mbt",
        )
    )

    result = manager.evaluate(
        create_trade_request(
            symbol="MBT",
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.APPROVED


def test_order_quantity_at_limit_is_approved() -> None:
    """Exactly maximum opening order size should remain valid."""

    manager = create_manager(
        max_order_quantity=5
    )

    result = manager.evaluate(
        create_trade_request(
            quantity=5,
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.APPROVED


def test_projected_long_position_over_limit_is_rejected() -> None:
    """Opening trade may not exceed long position limit."""

    manager = create_manager(
        max_absolute_position=10
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN",
            quantity=5,
        ),
        current_position=8,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == 13


def test_projected_short_position_over_limit_is_rejected() -> None:
    """Opening trade may not exceed short position limit."""

    manager = create_manager(
        max_absolute_position=10
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_OPEN",
            quantity=5,
        ),
        current_position=-8,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == -13


def test_projected_position_at_limit_is_approved() -> None:
    """Exactly configured absolute position limit is valid."""

    manager = create_manager(
        max_absolute_position=10
    )

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_OPEN",
            quantity=2,
        ),
        current_position=8,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 10


def test_sell_to_close_reduces_long_position() -> None:
    """SELL_TO_CLOSE should reduce existing long exposure."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=2,
        ),
        current_position=5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == 3


def test_buy_to_close_reduces_short_position() -> None:
    """BUY_TO_CLOSE should reduce existing short exposure."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=2,
        ),
        current_position=-5,
    )

    assert result.status is RiskDecisionStatus.APPROVED
    assert result.projected_position == -3


def test_sell_to_close_without_long_position_is_rejected() -> None:
    """SELL_TO_CLOSE requires an existing long."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "existing long position" in result.reason.lower()


def test_buy_to_close_without_short_position_is_rejected() -> None:
    """BUY_TO_CLOSE requires an existing short."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
        ),
        current_position=0,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert "existing short position" in result.reason.lower()


def test_sell_to_close_cannot_cross_through_flat() -> None:
    """Closing a long must not accidentally create a short."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="SELL_TO_CLOSE",
            quantity=3,
        ),
        current_position=2,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == -1
    assert "cross through flat" in result.reason.lower()


def test_buy_to_close_cannot_cross_through_flat() -> None:
    """Closing a short must not accidentally create a long."""

    manager = create_manager()

    result = manager.evaluate(
        create_trade_request(
            intent_value="BUY_TO_CLOSE",
            quantity=3,
        ),
        current_position=-2,
    )

    assert result.status is RiskDecisionStatus.REJECTED
    assert result.projected_position == 1
    assert "cross through flat" in result.reason.lower()


def test_manager_retains_kill_switch() -> None:
    """RiskManager should retain the supplied kill switch."""

    kill_switch = KillSwitch()

    manager = create_manager(
        kill_switch=kill_switch
    )

    assert manager.kill_switch is kill_switch


def test_manager_retains_daily_loss_guard() -> None:
    """RiskManager should retain supplied daily-loss guard."""

    guard = DailyLossGuard(
        5000
    )

    manager = create_manager(
        daily_loss_guard=guard
    )

    assert manager.daily_loss_guard is guard


def test_invalid_trade_request_type_is_rejected() -> None:
    """RiskManager requires a TradeRequest."""

    manager = create_manager()

    with pytest.raises(
        TypeError,
        match="'trade_request' must be a TradeRequest",
    ):
        manager.evaluate(
            object(),  # type: ignore[arg-type]
            current_position=0,
        )


def test_invalid_current_position_is_rejected() -> None:
    """Current broker position must be signed integer."""

    manager = create_manager()

    with pytest.raises(
        TypeError,
        match="'current_position' must be an integer",
    ):
        manager.evaluate(
            create_trade_request(),
            current_position=1.5,  # type: ignore[arg-type]
        )


def test_invalid_controls_are_rejected() -> None:
    """RiskManager requires TradingControls."""

    with pytest.raises(
        TypeError,
        match="'controls' must be a TradingControls",
    ):
        RiskManager(
            object(),  # type: ignore[arg-type]
            KillSwitch(),
            DailyLossGuard(5000),
        )


def test_invalid_kill_switch_is_rejected() -> None:
    """RiskManager requires a KillSwitch."""

    with pytest.raises(
        TypeError,
        match="'kill_switch' must be a KillSwitch",
    ):
        RiskManager(
            create_controls(),
            object(),  # type: ignore[arg-type]
            DailyLossGuard(5000),
        )


def test_invalid_daily_loss_guard_is_rejected() -> None:
    """RiskManager requires a DailyLossGuard."""

    with pytest.raises(
        TypeError,
        match="'daily_loss_guard' must be a DailyLossGuard",
    ):
        RiskManager(
            create_controls(),
            KillSwitch(),
            object(),  # type: ignore[arg-type]
        )


def test_empty_allowed_symbols_are_rejected() -> None:
    """RiskManager requires at least one permitted symbol."""

    with pytest.raises(
        ValueError,
        match="at least one symbol",
    ):
        RiskManager(
            create_controls(),
            KillSwitch(),
            DailyLossGuard(5000),
            allowed_symbols=(),
        )


def test_invalid_max_order_quantity_is_rejected() -> None:
    """Maximum opening order quantity must be positive."""

    with pytest.raises(
        ValueError,
        match="'max_order_quantity' must be a positive integer",
    ):
        RiskManager(
            create_controls(),
            KillSwitch(),
            DailyLossGuard(5000),
            max_order_quantity=0,
        )


def test_invalid_max_position_is_rejected() -> None:
    """Maximum absolute position must be positive."""

    with pytest.raises(
        ValueError,
        match="'max_absolute_position' must be a positive integer",
    ):
        RiskManager(
            create_controls(),
            KillSwitch(),
            DailyLossGuard(5000),
            max_absolute_position=0,
        )