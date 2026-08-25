"""Continuous real Eagle LIVE -> IB paper trader.

Version 1 continuously listens for Eagle Fund signals and executes only
BTCUSDT entry/exit lifecycle events as operator-configured MBT paper orders.

fund.update frames are recognized and logged, but stop/trailing-stop
management is intentionally disabled in Version 1. Updates never create a
TradeRequest or broker order.

Safety rules:
- Eagle environment must be live.
- TWS paper endpoint only.
- BTCUSDT -> MBT only.
- Operator explicitly selects MBT contract month, local symbol, and quantity.
- Configurable quantity is capped at 10 MBT; no pyramiding.
- Historical replay may update durable BTS state but can never submit to IB.
- A post-replay heartbeat is required before live execution.
- Broker/lifecycle/execution state must reconcile before each order.
- Unarmed live observation does not mutate lifecycle, consume lifecycle
  events, advance the durable cursor, run RiskManager, or submit to IB.
- Unknown/unexpected states fail closed.
- Broker submission requires --confirm-continuous-paper.
"""

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import time

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.eagle_trade_adapter import (
    EagleTradeAdaptStatus,
    EagleTradeAdapter,
)
from app.communications.eagle_update import EagleUpdate
from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import TradeIntent
from app.daily_loss_guard import DailyLossGuard
from app.duplicate_order_guard import DuplicateOrderGuard
from app.event_processor import EventProcessStatus, EventProcessor
from app.event_store import EventProcessingResult, EventStore
from app.execution_ledger import ExecutionLedger, ExecutionRecord, ExecutionStatus
from app.heartbeat_processor import HeartbeatProcessor
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.ib_execution_client import IBExecutionClient
from app.ib_order_factory import IBOrderFactory
from app.ib_trading_readiness import IBTradingReadiness
from app.kill_switch import KillSwitch
from app.risk_manager import RiskManager
from app.signal_lifecycle_guard import SignalLifecycleGuard, SignalLifecycleState
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


EAGLE_URI = "wss://tracer.eagleailabs.com/ipc-api/ipc/v1/fund/stream"
EAGLE_API_KEY_ENVIRONMENT_VARIABLE = "BTS_EAGLE_LIVE_API_KEY"

IB_HOST = "127.0.0.1"
IB_PORT = 7497
IB_CLIENT_ID = 1

SYMBOL = "MBT"
EXCHANGE = "CME"
CURRENCY = "USD"
TRADING_CLASS = "MBT"

MAX_CONFIGURABLE_QUANTITY = 10
STOP_LOSS_POINTS = Decimal("500")
MAX_DAILY_LOSS = Decimal("1000")

DEFAULT_EVENT_DATABASE = Path("data") / "real_eagle_live_events.db"
DEFAULT_LIFECYCLE_DATABASE = Path("data") / "real_eagle_live_signals.db"
DEFAULT_EXECUTION_LEDGER = Path("data") / "real_eagle_live_execution.db"

CONNECTION_TIMEOUT_SECONDS = 10.0
EXECUTION_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_MESSAGES = 0
ARMING_ARGUMENT = "--confirm-continuous-paper"
RECOVERY_ARGUMENT = "--recover-reserved-exit"
SUPPORTED_EAGLE_SYMBOL = "BTCUSDT"


@dataclass(frozen=True, slots=True)
class RuntimeExecutionConfig:
    """Validated operator-selected MBT execution configuration."""

    contract_month: str
    local_symbol: str
    quantity: int


def validate_runtime_execution_config(
    *,
    contract_month: str,
    local_symbol: str,
    quantity: int,
) -> RuntimeExecutionConfig:
    """Validate one explicit operator-selected execution configuration."""

    if not isinstance(contract_month, str) or not contract_month.strip():
        raise ValueError("'contract_month' must be a non-empty string.")

    normalized_contract_month = contract_month.strip()
    if (
        not normalized_contract_month.isdigit()
        or len(normalized_contract_month) not in {6, 8}
    ):
        raise ValueError(
            "'contract_month' must contain 6 or 8 numeric characters."
        )

    if not isinstance(local_symbol, str) or not local_symbol.strip():
        raise ValueError("'local_symbol' must be a non-empty string.")

    normalized_local_symbol = local_symbol.strip().upper()

    if (
        not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or quantity < 1
        or quantity > MAX_CONFIGURABLE_QUANTITY
    ):
        raise ValueError(
            "'quantity' must be an integer from 1 through "
            f"{MAX_CONFIGURABLE_QUANTITY}."
        )

    return RuntimeExecutionConfig(
        contract_month=normalized_contract_month,
        local_symbol=normalized_local_symbol,
        quantity=quantity,
    )


@dataclass(frozen=True, slots=True)
class DurableOpenSignal:
    """One durable BTS signal that currently represents an open trade."""

    signal_id: str
    state: SignalLifecycleState
    last_event_id: str


@dataclass(frozen=True, slots=True)
class ReservedExitRecord:
    """Normalized durable RESERVED exit used by recovery checks."""

    event_id: str
    signal_id: str
    symbol: str
    intent: TradeIntent
    quantity: int
    status: ExecutionStatus
    broker_order_id: int | None


@dataclass(frozen=True, slots=True)
class ReservedExitRecoveryDecision:
    """Decision describing whether one RESERVED exit may be recovered."""

    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ContinuousPaperResult:
    """Immutable result when a bounded runner session ends."""

    armed: bool
    hello_received: bool
    staging_confirmed: bool
    replay_expected: int
    replay_processed: int
    replay_complete: bool
    post_replay_heartbeat_seen: bool
    heartbeats: int
    lifecycle_events: int
    btc_events_adapted: int
    non_btc_events_ignored: int
    approved_decisions: int
    rejected_decisions: int
    broker_submissions: int
    filled_orders: int
    final_mbt_position: int
    durable_open_signal_count: int
    kill_switch_active: bool
    final_eagle_cursor: int | None


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> None:
    """Wait for one asynchronous IB condition."""

    if not callable(condition):
        raise TypeError("'condition' must be callable.")

    if not isinstance(description, str) or not description.strip():
        raise ValueError("'description' must be a non-empty string.")

    deadline = time.monotonic() + float(timeout_seconds)

    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {description.strip()}.")
        time.sleep(poll_interval_seconds)


def wait_for_execution_resolution(
    *,
    execution_ledger: ExecutionLedger,
    event_id: str,
    kill_switch: KillSwitch,
    timeout_seconds: float,
) -> ExecutionRecord:
    """Wait for one submitted paper order to reach terminal state."""

    def resolved() -> bool:
        if kill_switch.active:
            return True

        record = execution_ledger.get(event_id)
        if record is None:
            return False

        return record.status in {
            ExecutionStatus.FILLED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }

    wait_until(
        resolved,
        description="IB paper execution resolution",
        timeout_seconds=timeout_seconds,
    )

    if kill_switch.active:
        raise RuntimeError(
            "BTS kill switch activated while waiting for IB paper execution: "
            f"{kill_switch.reason}"
        )

    record = execution_ledger.get(event_id)
    if record is None:
        raise RuntimeError(
            "Submitted execution disappeared from the durable execution ledger."
        )

    return record


def refresh_position_snapshot(
    *,
    app: IBApiPositionApp,
    manager: IBConnectionManager,
    broker_client: IBBrokerClient,
) -> None:
    """Obtain a fresh completed TWS position snapshot."""

    if app.position_request_active:
        app.cancel_position_updates()

    manager.request_position_snapshot()

    wait_until(
        lambda: broker_client.snapshot_complete,
        description="IB position snapshot completion",
        timeout_seconds=CONNECTION_TIMEOUT_SECONDS,
    )


def get_relevant_btc_eagle_open_positions(
    open_positions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Return Eagle hello open positions relevant to the BTC-only runner.

    Valid non-BTC positions are intentionally ignored. Any open-position
    snapshot whose symbol is missing, blank, or non-string is ambiguous and
    therefore fails closed.
    """

    relevant_positions: list[dict[str, object]] = []

    for position in open_positions:
        raw_symbol = position.get("symbol")

        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise RuntimeError(
                "Eagle hello open position must contain a non-empty "
                "string symbol; startup safety cannot classify this position."
            )

        normalized_symbol = raw_symbol.strip().upper()

        if normalized_symbol == SUPPORTED_EAGLE_SYMBOL:
            relevant_positions.append(position)

    return tuple(relevant_positions)


def get_missed_eagle_signal_ids(
    *,
    relevant_eagle_open_positions: tuple[dict[str, object], ...],
    broker_position: int,
    open_signals: tuple["DurableOpenSignal", ...],
) -> frozenset[str]:
    """Identify Eagle BTC positions that BTS missed while it was offline.

    A position is considered missed only when BTS and the broker are both
    flat. In that case BTS deliberately does not chase the existing Eagle
    trade. The returned signal IDs are used to suppress replay lifecycle
    mutation and to ignore the eventual exit.

    When BTS or the broker already has exposure, this function returns an
    empty set so the normal strict Eagle/BTS/TWS reconciliation remains in
    force.
    """

    if broker_position != 0 or open_signals:
        return frozenset()

    signal_ids: list[str] = []

    for position in relevant_eagle_open_positions:
        raw_signal_id = position.get("signal_id")

        if (
            not isinstance(raw_signal_id, str)
            or not raw_signal_id.strip()
        ):
            raise RuntimeError(
                "Eagle hello BTCUSDT open position must contain a "
                "non-empty signal_id before BTS can classify it as missed."
            )

        signal_ids.append(
            raw_signal_id.strip()
        )

    if len(set(signal_ids)) != len(signal_ids):
        raise RuntimeError(
            "Eagle hello contains duplicate BTCUSDT signal IDs; "
            "missed-trade classification is ambiguous."
        )

    return frozenset(signal_ids)


def require_eagle_hello_reconciled(
    *,
    relevant_eagle_open_positions: tuple[dict[str, object], ...],
    broker_position: int,
    open_signals: tuple[DurableOpenSignal, ...],
    expected_quantity: int,
) -> None:
    """Require Eagle hello, BTS lifecycle, and broker exposure to agree.

    A fund.hello frame is a snapshot/control frame, never a trading
    instruction. Startup may continue with an existing BTC position only when
    Eagle's one relevant open signal exactly matches the already-reconciled
    BTS durable signal and broker direction.

    Any ambiguity or disagreement fails closed.
    """

    if (
        not isinstance(expected_quantity, int)
        or isinstance(expected_quantity, bool)
        or expected_quantity < 1
        or expected_quantity > MAX_CONFIGURABLE_QUANTITY
    ):
        raise ValueError(
            "'expected_quantity' is outside the approved range."
        )

    if len(relevant_eagle_open_positions) > 1:
        raise RuntimeError(
            "Eagle hello contains more than one relevant BTCUSDT open "
            "position; startup reconciliation is ambiguous."
        )

    if broker_position == 0 and not open_signals:
        if relevant_eagle_open_positions:
            raise RuntimeError(
                "Eagle hello contains a relevant BTCUSDT open position "
                "but BTS and broker are flat."
            )
        return

    if broker_position == 0:
        raise RuntimeError(
            "Eagle hello startup reconciliation found BTS open lifecycle "
            "state while broker is flat."
        )

    if len(open_signals) != 1:
        raise RuntimeError(
            "Eagle hello startup reconciliation requires exactly one "
            "durable BTS open signal when broker is positioned."
        )

    if broker_position not in {-expected_quantity, expected_quantity}:
        raise RuntimeError(
            "Eagle hello startup reconciliation found broker position "
            "outside the approved runtime quantity."
        )

    if len(relevant_eagle_open_positions) != 1:
        raise RuntimeError(
            "BTS and broker have an open MBT position but Eagle hello "
            "does not contain exactly one matching BTCUSDT open position."
        )

    eagle_open = relevant_eagle_open_positions[0]

    raw_signal_id = eagle_open.get("signal_id")
    if not isinstance(raw_signal_id, str) or not raw_signal_id.strip():
        raise RuntimeError(
            "Eagle hello BTCUSDT open position must contain a non-empty "
            "signal_id."
        )
    eagle_signal_id = raw_signal_id.strip()

    raw_direction = eagle_open.get("direction")
    if not isinstance(raw_direction, str) or not raw_direction.strip():
        raise RuntimeError(
            "Eagle hello BTCUSDT open position must contain a non-empty "
            "direction."
        )

    eagle_direction = raw_direction.strip().lower()
    if eagle_direction not in {"long", "short"}:
        raise RuntimeError(
            "Eagle hello BTCUSDT open position has unsupported direction "
            f"{raw_direction!r}."
        )

    durable_open = open_signals[0]

    if eagle_signal_id != durable_open.signal_id:
        raise RuntimeError(
            "Eagle hello BTCUSDT signal_id does not match the durable "
            "BTS open signal."
        )

    if broker_position == expected_quantity:
        expected_direction = "long"
        expected_state = SignalLifecycleState.LONG_OPEN
    else:
        expected_direction = "short"
        expected_state = SignalLifecycleState.SHORT_OPEN

    if durable_open.state is not expected_state:
        raise RuntimeError(
            "Eagle hello startup reconciliation found durable BTS "
            "direction inconsistent with broker position."
        )

    if eagle_direction != expected_direction:
        raise RuntimeError(
            "Eagle hello BTCUSDT direction does not match the reconciled "
            "BTS/broker position."
        )


def get_mbt_position(
    broker_client: IBBrokerClient,
    *,
    expected_local_symbol: str | None = None,
) -> int:
    """Return signed position for the operator-approved MBT contract.

    IBBrokerClient exposes normalized RawBrokerPosition objects. Their
    ``symbol`` field contains the broker-position identity, which may be the
    root symbol (``MBT``) or the contract-local symbol (for example
    ``MBTQ6``). The original IB callback's separate local_symbol field is not
    preserved on RawBrokerPosition.
    """

    if not isinstance(broker_client, IBBrokerClient):
        raise TypeError("'broker_client' must be an IBBrokerClient.")

    normalized_expected = (
        expected_local_symbol.strip().upper()
        if isinstance(expected_local_symbol, str)
        and expected_local_symbol.strip()
        else None
    )

    if expected_local_symbol is not None and normalized_expected is None:
        raise ValueError("'expected_local_symbol' must be a non-empty string.")

    allowed_symbols = {SYMBOL}
    if normalized_expected is not None:
        allowed_symbols.add(normalized_expected)

    total = 0

    for position in broker_client.get_raw_positions():
        normalized_symbol = position.symbol.strip().upper()

        if normalized_symbol in allowed_symbols:
            total += position.quantity

    return total


def require_no_other_broker_positions(
    broker_client: IBBrokerClient,
    *,
    expected_local_symbol: str | None = None,
) -> None:
    """Require the account to contain only the approved MBT contract, or be flat.

    IBBrokerClient returns normalized RawBrokerPosition objects whose
    ``symbol`` field may contain either ``MBT`` or the IB local symbol for the
    approved contract.
    """

    normalized_expected = (
        expected_local_symbol.strip().upper()
        if isinstance(expected_local_symbol, str)
        and expected_local_symbol.strip()
        else None
    )

    if expected_local_symbol is not None and normalized_expected is None:
        raise ValueError("'expected_local_symbol' must be a non-empty string.")

    allowed_symbols = {SYMBOL}
    if normalized_expected is not None:
        allowed_symbols.add(normalized_expected)

    for position in broker_client.get_raw_positions():
        normalized_symbol = position.symbol.strip().upper()

        if normalized_symbol not in allowed_symbols:
            raise RuntimeError(
                "Continuous paper trader requires no unrelated broker positions."
            )


def load_durable_open_signals(
    lifecycle_database_path: str | Path,
) -> tuple[DurableOpenSignal, ...]:
    """Read durable LONG_OPEN / SHORT_OPEN lifecycle state."""

    connection = sqlite3.connect(Path(lifecycle_database_path))

    try:
        rows = connection.execute(
            """
            SELECT signal_id, state, last_event_id
            FROM signal_lifecycle
            WHERE state IN ('LongOpen', 'ShortOpen')
            ORDER BY signal_id
            """
        ).fetchall()
    finally:
        connection.close()

    results: list[DurableOpenSignal] = []

    for signal_id, state_text, last_event_id in rows:
        if state_text == "LongOpen":
            state = SignalLifecycleState.LONG_OPEN
        elif state_text == "ShortOpen":
            state = SignalLifecycleState.SHORT_OPEN
        else:
            raise RuntimeError(
                f"Unsupported durable open lifecycle state: {state_text!r}."
            )

        results.append(
            DurableOpenSignal(
                signal_id=signal_id,
                state=state,
                last_event_id=last_event_id,
            )
        )

    return tuple(results)


def execution_state_clear(execution_ledger: ExecutionLedger) -> bool:
    """Return True when every durable execution record is terminal."""

    return all(record.terminal for record in execution_ledger.all_records())


def require_execution_state_clear(execution_ledger: ExecutionLedger) -> None:
    """Fail closed if any execution has an uncertain/nonterminal state."""

    unresolved = tuple(
        record for record in execution_ledger.all_records() if not record.terminal
    )

    if unresolved:
        event_ids = ", ".join(record.event_id for record in unresolved)
        raise RuntimeError(
            "Continuous paper trading blocked by unresolved execution state: "
            f"{event_ids}."
        )


def find_reserved_exit(
    execution_ledger: ExecutionLedger,
) -> ReservedExitRecord | None:
    """Return the one recoverable RESERVED closing execution, if present.

    Recovery is intentionally limited to closing orders. A RESERVED
    opening order is never automatically recoverable because BTS cannot
    know whether the operator still wants to establish that exposure
    after a broker outage.

    More than one RESERVED closing execution is treated as an unsafe
    ambiguous state and fails closed.
    """

    if not isinstance(
        execution_ledger,
        ExecutionLedger,
    ):
        raise TypeError(
            "'execution_ledger' must be an ExecutionLedger."
        )

    reserved_records = tuple(
        record
        for record in execution_ledger.all_records()
        if record.status is ExecutionStatus.RESERVED
    )

    if not reserved_records:
        return None

    reserved_openings = tuple(
        record
        for record in reserved_records
        if TradeIntent(record.intent)
        in {
            TradeIntent.BUY_TO_OPEN,
            TradeIntent.SELL_TO_OPEN,
        }
    )

    if reserved_openings:
        event_ids = ", ".join(
            record.event_id
            for record in reserved_openings
        )

        raise RuntimeError(
            "RESERVED opening execution cannot be recovered "
            "automatically. "
            f"Events: {event_ids}."
        )

    reserved_exits = tuple(
        record
        for record in reserved_records
        if TradeIntent(record.intent)
        in {
            TradeIntent.SELL_TO_CLOSE,
            TradeIntent.BUY_TO_CLOSE,
        }
    )

    if len(reserved_exits) > 1:
        event_ids = ", ".join(
            record.event_id
            for record in reserved_exits
        )

        raise RuntimeError(
            "More than one RESERVED exit exists; "
            "recovery is ambiguous and blocked. "
            f"Events: {event_ids}."
        )

    if len(reserved_exits) == 1:
        record = reserved_exits[0]

        return ReservedExitRecord(
            event_id=record.event_id,
            signal_id=record.signal_id,
            symbol=record.symbol,
            intent=TradeIntent(record.intent),
            quantity=record.quantity,
            status=record.status,
            broker_order_id=record.broker_order_id,
        )

    return None


def evaluate_reserved_exit_recovery(
    *,
    trade_request,
    broker_position: int,
    open_signals: tuple[DurableOpenSignal, ...],
    recovery_authorized: bool,
    expected_quantity: int = 1,
) -> ReservedExitRecoveryDecision:
    """Determine whether a RESERVED exit is safe to recover.

    Recovery requires all of the following:

    - explicit operator authorization;
    - exactly one durable open BTS signal;
    - exact signal-ID match;
    - closing intent only;
    - broker exposure on the expected side;
    - lifecycle direction matching the closing intent.

    This function makes no broker call and mutates no durable state.
    """

    if not isinstance(
        recovery_authorized,
        bool,
    ):
        raise TypeError(
            "'recovery_authorized' must be a bool."
        )

    if not recovery_authorized:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Reserved exit recovery requires explicit "
                "operator authorization."
            ),
        )

    if trade_request.symbol != SYMBOL:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Reserved exit recovery permits MBT only."
            ),
        )

    if trade_request.quantity != expected_quantity:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Reserved exit recovery quantity does not match the "
                f"approved runtime quantity {expected_quantity}."
            ),
        )

    intent = trade_request.intent

    if intent not in {
        TradeIntent.SELL_TO_CLOSE,
        TradeIntent.BUY_TO_CLOSE,
    }:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Reserved exit recovery cannot recover "
                "an opening order."
            ),
        )

    if broker_position == 0:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Broker is already flat; no recovery "
                "closing order may be submitted."
            ),
        )

    if broker_position not in {-expected_quantity, expected_quantity}:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Broker position is outside the permitted runtime recovery "
                f"state +/-{expected_quantity} MBT."
            ),
        )

    if len(open_signals) != 1:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Reserved exit recovery requires exactly "
                "one durable open signal."
            ),
        )

    open_signal = open_signals[0]

    if open_signal.signal_id != trade_request.signal_id:
        return ReservedExitRecoveryDecision(
            allowed=False,
            reason=(
                "Reserved exit signal ID does not match "
                "the durable open signal."
            ),
        )

    if intent is TradeIntent.SELL_TO_CLOSE:
        if broker_position != expected_quantity:
            return ReservedExitRecoveryDecision(
                allowed=False,
                reason=(
                    "SELL_TO_CLOSE recovery requires broker "
                    f"position +{expected_quantity} MBT."
                ),
            )

        if (
            open_signal.state
            is not SignalLifecycleState.LONG_OPEN
        ):
            return ReservedExitRecoveryDecision(
                allowed=False,
                reason=(
                    "SELL_TO_CLOSE recovery requires durable "
                    "LONG_OPEN lifecycle."
                ),
            )

    elif intent is TradeIntent.BUY_TO_CLOSE:
        if broker_position != -expected_quantity:
            return ReservedExitRecoveryDecision(
                allowed=False,
                reason=(
                    "BUY_TO_CLOSE recovery requires broker "
                    f"position -{expected_quantity} MBT."
                ),
            )

        if (
            open_signal.state
            is not SignalLifecycleState.SHORT_OPEN
        ):
            return ReservedExitRecoveryDecision(
                allowed=False,
                reason=(
                    "BUY_TO_CLOSE recovery requires durable "
                    "SHORT_OPEN lifecycle."
                ),
            )

    return ReservedExitRecoveryDecision(
        allowed=True,
        reason=(
            "Reserved exit matches broker position, "
            "durable lifecycle, signal ID, and explicit "
            "operator recovery authorization."
        ),
    )


def reconcile_broker_and_lifecycle(
    *,
    broker_client: IBBrokerClient,
    lifecycle_database_path: str | Path,
    expected_local_symbol: str | None = None,
    expected_quantity: int = 1,
) -> tuple[int, tuple[DurableOpenSignal, ...]]:
    """Require TWS position and durable signal state to agree exactly."""

    if (
        not isinstance(expected_quantity, int)
        or isinstance(expected_quantity, bool)
        or expected_quantity < 1
        or expected_quantity > MAX_CONFIGURABLE_QUANTITY
    ):
        raise ValueError("'expected_quantity' is outside the approved range.")

    require_no_other_broker_positions(
        broker_client,
        expected_local_symbol=expected_local_symbol,
    )

    broker_position = get_mbt_position(
        broker_client,
        expected_local_symbol=expected_local_symbol,
    )

    if broker_position not in {-expected_quantity, 0, expected_quantity}:
        raise RuntimeError(
            "Continuous paper trader permits broker MBT position only flat or "
            f"+/-{expected_quantity}."
        )

    open_signals = load_durable_open_signals(lifecycle_database_path)

    if len(open_signals) > 1:
        raise RuntimeError(
            "Continuous paper trader found more than one durable open signal."
        )

    if broker_position == 0:
        if open_signals:
            raise RuntimeError(
                "Position reconciliation mismatch: broker is flat but BTS has an "
                "open durable signal."
            )
        return broker_position, open_signals

    if not open_signals:
        raise RuntimeError(
            "Position reconciliation mismatch: broker has MBT position but BTS has "
            "no durable open signal."
        )

    open_signal = open_signals[0]

    if (
        broker_position == expected_quantity
        and open_signal.state is not SignalLifecycleState.LONG_OPEN
    ):
        raise RuntimeError(
            f"Position reconciliation mismatch: broker is +{expected_quantity} MBT but durable signal "
            "is not LONG_OPEN."
        )

    if (
        broker_position == -expected_quantity
        and open_signal.state is not SignalLifecycleState.SHORT_OPEN
    ):
        raise RuntimeError(
            f"Position reconciliation mismatch: broker is -{expected_quantity} MBT but durable signal "
            "is not SHORT_OPEN."
        )

    return broker_position, open_signals


def validate_trade_request_against_position(
    *,
    trade_request,
    broker_position: int,
    open_signals: tuple[DurableOpenSignal, ...],
    expected_quantity: int = 1,
) -> None:
    """Apply continuous-runner position/lifecycle policy."""

    if trade_request.symbol != SYMBOL:
        raise RuntimeError("Continuous paper trader permits MBT only.")

    if trade_request.quantity != expected_quantity:
        raise RuntimeError(
            "TradeRequest quantity does not match approved runtime quantity "
            f"{expected_quantity}."
        )

    intent = trade_request.intent

    if intent in {TradeIntent.BUY_TO_OPEN, TradeIntent.SELL_TO_OPEN}:
        if broker_position != 0:
            raise RuntimeError("Opening order blocked because broker is not flat.")
        if open_signals:
            raise RuntimeError(
                "Opening order blocked because BTS already has a durable open signal."
            )
        return

    if intent is TradeIntent.SELL_TO_CLOSE:
        if broker_position != expected_quantity:
            raise RuntimeError(
                f"SELL_TO_CLOSE requires current broker position +{expected_quantity} MBT."
            )
        if len(open_signals) != 1:
            raise RuntimeError(
                "SELL_TO_CLOSE requires exactly one durable open signal."
            )
        if open_signals[0].state is not SignalLifecycleState.LONG_OPEN:
            raise RuntimeError("SELL_TO_CLOSE requires durable LONG_OPEN lifecycle.")
        if open_signals[0].signal_id != trade_request.signal_id:
            raise RuntimeError(
                "SELL_TO_CLOSE signal does not match the durable open long signal."
            )
        return

    if intent is TradeIntent.BUY_TO_CLOSE:
        if broker_position != -expected_quantity:
            raise RuntimeError(
                f"BUY_TO_CLOSE requires current broker position -{expected_quantity} MBT."
            )
        if len(open_signals) != 1:
            raise RuntimeError(
                "BUY_TO_CLOSE requires exactly one durable open signal."
            )
        if open_signals[0].state is not SignalLifecycleState.SHORT_OPEN:
            raise RuntimeError("BUY_TO_CLOSE requires durable SHORT_OPEN lifecycle.")
        if open_signals[0].signal_id != trade_request.signal_id:
            raise RuntimeError(
                "BUY_TO_CLOSE signal does not match the durable open short signal."
            )
        return

    raise RuntimeError(
        f"Unsupported TradeIntent for continuous paper trading: {intent!r}."
    )


def expected_position_after_trade(trade_request) -> int:
    """Return expected broker MBT position after one filled request."""

    if trade_request.intent is TradeIntent.BUY_TO_OPEN:
        return trade_request.quantity
    if trade_request.intent is TradeIntent.SELL_TO_OPEN:
        return -trade_request.quantity
    if trade_request.intent in {TradeIntent.SELL_TO_CLOSE, TradeIntent.BUY_TO_CLOSE}:
        return 0
    raise RuntimeError("Unsupported TradeIntent.")


def expected_ib_action(trade_request) -> str:
    """Return expected IB BUY/SELL action."""

    if trade_request.intent in {TradeIntent.BUY_TO_OPEN, TradeIntent.BUY_TO_CLOSE}:
        return "BUY"
    if trade_request.intent in {TradeIntent.SELL_TO_OPEN, TradeIntent.SELL_TO_CLOSE}:
        return "SELL"
    raise RuntimeError("Unsupported TradeIntent.")


def _record_replay_frame(
    *,
    replay_processed: int,
    replay_expected: int,
) -> tuple[int, bool]:
    """Count one delivered replay frame and report completion."""

    replay_processed += 1
    return replay_processed, replay_processed >= replay_expected


async def run_continuous_paper_trader(
    *,
    armed: bool,
    api_key: str,
    event_database_path: str | Path,
    lifecycle_database_path: str | Path,
    execution_ledger_path: str | Path,
    max_messages: int,
    execution_config: RuntimeExecutionConfig,
    recover_reserved_exit: bool = False,
) -> ContinuousPaperResult:
    """Run the continuous Eagle LIVE -> TWS paper trader."""

    if not isinstance(armed, bool):
        raise TypeError("'armed' must be a bool.")

    if not isinstance(recover_reserved_exit, bool):
        raise TypeError("'recover_reserved_exit' must be a bool.")

    if not isinstance(execution_config, RuntimeExecutionConfig):
        raise TypeError("'execution_config' must be a RuntimeExecutionConfig.")

    if (
        not isinstance(max_messages, int)
        or isinstance(max_messages, bool)
        or max_messages < 0
    ):
        raise ValueError("'max_messages' must be a non-negative integer.")

    event_store = EventStore(event_database_path)
    event_processor = EventProcessor(event_store)
    heartbeat_processor = HeartbeatProcessor(event_store)
    lifecycle_guard = SignalLifecycleGuard(lifecycle_database_path)
    adapter = EagleTradeAdapter(lifecycle_guard)
    execution_ledger = ExecutionLedger(execution_ledger_path)

    reserved_exit = find_reserved_exit(execution_ledger)

    if reserved_exit is None:
        require_execution_state_clear(execution_ledger)
    elif not recover_reserved_exit:
        raise RuntimeError(
            "Continuous paper trading blocked by RESERVED exit. "
            f"Restart with {RECOVERY_ARGUMENT} only after operator review."
        )

    kill_switch = KillSwitch()
    trading_controls = TradingControls(
        symbol=SYMBOL,
        quantity=execution_config.quantity,
        stop_loss_points=STOP_LOSS_POINTS,
    )
    trading_controls.resume()

    daily_loss_guard = DailyLossGuard(MAX_DAILY_LOSS)
    risk_manager = RiskManager(
        trading_controls,
        kill_switch,
        daily_loss_guard,
        allowed_symbols=(SYMBOL,),
        max_order_quantity=execution_config.quantity,
        max_absolute_position=execution_config.quantity,
    )

    coordinator = TradeCoordinator(
        controls=trading_controls,
        signal_lifecycle_guard=lifecycle_guard,
    )

    duplicate_guard = DuplicateOrderGuard()
    broker_client = IBBrokerClient()
    app = IBApiPositionApp(
        broker_client,
        execution_ledger=execution_ledger,
        kill_switch=kill_switch,
    )
    manager = IBConnectionManager(
        app,
        host=IB_HOST,
        port=IB_PORT,
        client_id=IB_CLIENT_ID,
        connection_timeout_seconds=CONNECTION_TIMEOUT_SECONDS,
    )

    order_factory = IBOrderFactory(
        exchange=EXCHANGE,
        currency=CURRENCY,
        trading_class=TRADING_CLASS,
        order_type="MKT",
        time_in_force="DAY",
        transmit=True,
    )

    execution_client = IBExecutionClient(
        order_factory=order_factory,
        duplicate_guard=duplicate_guard,
        execution_ledger=execution_ledger,
        place_order_function=app.placeOrder,
    )

    hello_received = False
    staging_confirmed = False
    replay_expected = 0
    replay_processed = 0
    replay_complete = False
    post_replay_heartbeat_seen = False
    heartbeats = 0
    lifecycle_events = 0
    btc_events_adapted = 0
    non_btc_events_ignored = 0
    approved_decisions = 0
    rejected_decisions = 0
    broker_submissions = 0
    filled_orders = 0
    messages_observed = 0
    final_mbt_position = 0

    # Signal IDs that Eagle says are currently open even though BTS and
    # the broker were flat at startup. These trades were missed while BTS
    # was offline and must never be chased or reconstructed into broker
    # exposure.
    missed_eagle_signal_ids: set[str] = set()

    try:
        manager.connect()

        wait_until(
            lambda: app.api_ready.ready,
            description="IB nextValidId handshake",
            timeout_seconds=CONNECTION_TIMEOUT_SECONDS,
        )

        if kill_switch.active:
            raise RuntimeError(
                "Kill switch activated during IB connection handshake: "
                f"{kill_switch.reason}"
            )

        refresh_position_snapshot(
            app=app,
            manager=manager,
            broker_client=broker_client,
        )

        # -------------------------------------------------------------
        # RESERVED-EXIT RECOVERY BOUNDARY
        # -------------------------------------------------------------
        if recover_reserved_exit:
            if not armed:
                raise RuntimeError(
                    "Reserved exit recovery requires continuous paper "
                    "execution to be explicitly armed."
                )

            reserved_exit = find_reserved_exit(execution_ledger)

            if reserved_exit is None:
                raise RuntimeError(
                    "Reserved exit recovery was requested, but no "
                    "RESERVED closing execution exists."
                )

            recovery_trade_request = execution_ledger.get_trade_request(
                reserved_exit.event_id
            )

            if recovery_trade_request is None:
                raise RuntimeError(
                    "RESERVED exit has no recoverable TradeRequest."
                )

            recovery_position = get_mbt_position(
                broker_client,
                expected_local_symbol=execution_config.local_symbol,
            )
            recovery_open_signals = load_durable_open_signals(
                lifecycle_database_path
            )

            recovery_decision = evaluate_reserved_exit_recovery(
                trade_request=recovery_trade_request,
                broker_position=recovery_position,
                open_signals=recovery_open_signals,
                recovery_authorized=recover_reserved_exit,
                expected_quantity=execution_config.quantity,
            )

            print()
            print("RESERVED EXIT RECOVERY CHECK")
            print("=" * 72)
            print(f"Event ID:       {reserved_exit.event_id}")
            print(f"Signal ID:      {reserved_exit.signal_id}")
            print(f"Broker position:{recovery_position:>8}")
            print(f"Recovery allowed: {recovery_decision.allowed}")
            print(f"Reason: {recovery_decision.reason}")
            print("=" * 72)

            if not recovery_decision.allowed:
                raise RuntimeError(
                    "RESERVED exit recovery rejected: "
                    f"{recovery_decision.reason}"
                )

            readiness = IBTradingReadiness(
                api_ready=app.api_ready,
                order_id_allocator=app.order_id_allocator,
                broker_client=broker_client,
                trading_controls=trading_controls,
                kill_switch=kill_switch,
            )

            readiness_result = readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )

            print(f"IB recovery readiness passed: {readiness_result.ready}")

            broker_order_id = app.order_id_allocator.allocate()

            recovery_submission = execution_client.submit_reserved(
                recovery_trade_request,
                contract_month=execution_config.contract_month,
                broker_order_id=broker_order_id,
            )
            broker_submissions += 1

            expected_action = expected_ib_action(recovery_trade_request)
            if recovery_submission.package.order.action != expected_action:
                raise RuntimeError(
                    "Recovered IB order action does not match TradeRequest."
                )

            if (
                recovery_submission.package.order.totalQuantity
                != execution_config.quantity
            ):
                raise RuntimeError(
                    "Recovered IB order quantity does not match approved "
                    "runtime quantity."
                )

            recovery_final_record = wait_for_execution_resolution(
                execution_ledger=execution_ledger,
                event_id=recovery_trade_request.event_id,
                kill_switch=kill_switch,
                timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
            )

            if recovery_final_record.status is not ExecutionStatus.FILLED:
                raise RuntimeError(
                    "Recovered exit did not reach FILLED. "
                    f"Status: {recovery_final_record.status.value}. "
                    f"Reason: {recovery_final_record.reason}"
                )

            filled_orders += 1

            recovery_lifecycle_decision = coordinator.commit_request(
                recovery_trade_request
            )

            if not recovery_lifecycle_decision.approved:
                raise RuntimeError(
                    "Recovered broker close FILLED but durable lifecycle "
                    "commit was rejected."
                )

            approved_decisions += 1

            refresh_position_snapshot(
                app=app,
                manager=manager,
                broker_client=broker_client,
            )

            recovered_position, recovered_open_signals = (
                reconcile_broker_and_lifecycle(
                    broker_client=broker_client,
                    lifecycle_database_path=lifecycle_database_path,
                    expected_local_symbol=execution_config.local_symbol,
                    expected_quantity=execution_config.quantity,
                )
            )

            if recovered_position != 0 or recovered_open_signals:
                raise RuntimeError(
                    "Recovered exit fill did not reconcile BTS and broker flat."
                )

            print()
            print("RESERVED EXIT RECOVERED, FILLED, AND RECONCILED.")
            print("Current MBT position: 0")
            print("Durable open signals: 0")

        starting_position, starting_open_signals = reconcile_broker_and_lifecycle(
            broker_client=broker_client,
            lifecycle_database_path=lifecycle_database_path,
            expected_local_symbol=execution_config.local_symbol,
            expected_quantity=execution_config.quantity,
        )

        print()
        print("CONTINUOUS PAPER TRADER PRE-FLIGHT")
        print("=" * 72)
        print("APPROVED MBT EXECUTION CONFIGURATION")
        print(f"Contract month:        {execution_config.contract_month}")
        print(f"Expected local symbol: {execution_config.local_symbol}")
        print(f"Order quantity:        {execution_config.quantity}")
        print(f"Hard quantity ceiling: {MAX_CONFIGURABLE_QUANTITY}")
        print("-" * 72)
        print(f"TWS API ready:             {app.api_ready.ready}")
        print(f"Next valid order ID:       {app.api_ready.next_valid_order_id}")
        print(f"Starting MBT position:     {starting_position}")
        print(f"Durable open signals:      {len(starting_open_signals)}")
        print(f"Execution state clear:     {execution_state_clear(execution_ledger)}")
        print(f"Kill switch active:        {kill_switch.active}")
        print(f"Continuous trading armed:  {armed}")
        print("=" * 72)

        eagle_client = EagleClient(
            uri=EAGLE_URI,
            api_key=api_key,
            since_seq=event_store.get_last_seq(),
        )

        print()
        print("REAL EAGLE -> IB CONTINUOUS PAPER TRADER")
        print("=" * 72)
        print(f"Eagle URI: {eagle_client._connection_uri()}")
        print("Environment required: LIVE")
        print("Historical replay orders: BLOCKED")
        print("Post-replay heartbeat required: YES")
        print("BTCUSDT -> MBT only")
        print(f"Maximum absolute MBT position: {execution_config.quantity}")
        print("fund.update: recognized; stop management disabled")

        if armed:
            print("MODE: CONTINUOUS PAPER EXECUTION ARMED")
        else:
            print("MODE: OBSERVE / REPLAY ONLY")
            print("NO BROKER SUBMISSION WILL OCCUR.")

        print("=" * 72)

        async for message in eagle_client.listen():
            messages_observed += 1
            print()
            print("-" * 72)

            if isinstance(message, EagleHello):
                hello_received = True
                staging_confirmed = message.environment.value == "live"
                replay_expected = message.replay_count
                replay_processed = 0
                replay_complete = replay_expected == 0
                post_replay_heartbeat_seen = False

                print("fund.hello received.")
                print(f"Environment:       {message.environment.value}")
                print(f"Replay expected:   {replay_expected}")
                print(f"Eagle open count:  {message.open_count}")
                print(f"Server last_seq:   {message.last_seq}")
                print(f"Requested since:   {message.since_seq}")

                if not staging_confirmed:
                    raise RuntimeError(
                        "Safety violation: continuous paper trader connected to "
                        "non-LIVE Eagle environment."
                    )

                relevant_eagle_open_positions = (
                    get_relevant_btc_eagle_open_positions(
                        message.open_positions
                    )
                )

                print(
                    "Relevant BTC Eagle opens: "
                    f"{len(relevant_eagle_open_positions)}"
                )

                missed_eagle_signal_ids = set(
                    get_missed_eagle_signal_ids(
                        relevant_eagle_open_positions=(
                            relevant_eagle_open_positions
                        ),
                        broker_position=starting_position,
                        open_signals=starting_open_signals,
                    )
                )

                if missed_eagle_signal_ids:
                    print()
                    print(
                        "EAGLE OPEN POSITION MISSED WHILE BTS WAS OFFLINE"
                    )
                    print(
                        "BTS and the broker are flat. Existing Eagle "
                        "BTC positions will NOT be chased."
                    )

                    for missed_signal_id in sorted(
                        missed_eagle_signal_ids
                    ):
                        print(
                            f"Missed Signal ID: {missed_signal_id}"
                        )

                    print(
                        "Replay for these signal IDs will be consumed "
                        "without creating BTS lifecycle exposure."
                    )
                    print(
                        "Their eventual exits will be consumed with "
                        "NO broker order."
                    )
                    print(
                        "BTS will trade the next fresh BTC fund.entry "
                        "normally."
                    )

                else:
                    require_eagle_hello_reconciled(
                        relevant_eagle_open_positions=(
                            relevant_eagle_open_positions
                        ),
                        broker_position=starting_position,
                        open_signals=starting_open_signals,
                        expected_quantity=execution_config.quantity,
                    )

                    print(
                        "Eagle/BTS/TWS startup reconciliation: PASSED"
                    )

            elif isinstance(message, EagleHeartbeat):
                heartbeats += 1

                # During armed operation, or before replay is complete, heartbeat
                # sequence may safely advance durable cursor. In unarmed live
                # observation we intentionally do not advance the cursor beyond an
                # unconsumed live lifecycle signal.
                if armed or not replay_complete:
                    heartbeat_processor.process(message)
                    durable_cursor_text = str(event_store.get_last_seq())
                else:
                    durable_cursor_text = (
                        f"{event_store.get_last_seq()} (not advanced in observe mode)"
                    )

                if replay_complete:
                    post_replay_heartbeat_seen = True

                print(f"fund.heartbeat seq {message.seq}")
                print(f"Replay complete: {replay_complete}")
                print(f"Post-replay heartbeat: {post_replay_heartbeat_seen}")
                print(f"Durable cursor: {durable_cursor_text}")

            elif isinstance(message, EagleUpdate):
                was_replay_update = not replay_complete

                print(f"fund.update seq {message.seq}")
                print(f"Signal ID: {message.signal_id}")
                print(f"Update type: {message.update_type}")
                print(f"Trail stop: {message.trail_stop}")
                print(
                    "Stop management not enabled; no broker action taken."
                )

                if was_replay_update:
                    update_result = event_store.check_and_mark_event_with_seq(
                        message.event_id,
                        message.seq,
                    )

                    if update_result is EventProcessingResult.ACCEPTED:
                        replay_processed, replay_complete = _record_replay_frame(
                            replay_processed=replay_processed,
                            replay_expected=replay_expected,
                        )

                        if replay_complete:
                            print("Historical Eagle replay is now complete.")
                    else:
                        # The frame still belongs to Eagle's announced replay.
                        # Count delivered replay frames even when BTS already knew it.
                        replay_processed, replay_complete = _record_replay_frame(
                            replay_processed=replay_processed,
                            replay_expected=replay_expected,
                        )

                        print(
                            "fund.update replay status: "
                            f"{update_result.value}."
                        )

                        if replay_complete:
                            print("Historical Eagle replay is now complete.")

                elif armed:
                    # In armed Version 1, updates are deliberately consumed and
                    # durably advanced even though no broker stop action occurs.
                    event_store.check_and_mark_event_with_seq(
                        message.event_id,
                        message.seq,
                    )
                else:
                    print(
                        "OBSERVE MODE - fund.update was not persisted; "
                        "durable cursor unchanged."
                    )

            elif isinstance(message, IncomingLifecycleEvent):
                lifecycle_events += 1
                was_replay_event = not replay_complete

                # -------------------------------------------------------------
                # REPLAY lifecycle events are durably processed in both modes.
                # They may rebuild lifecycle state, but can never reach IB.
                # -------------------------------------------------------------
                if was_replay_event:
                    event_result = event_processor.process(message)

                    print(f"Lifecycle: {message.message_type}")
                    print(f"Seq:       {message.seq}")
                    print(f"Signal ID: {message.signal_id}")
                    print(f"Event status: {event_result.status.value}")

                    replay_processed, replay_complete = _record_replay_frame(
                        replay_processed=replay_processed,
                        replay_expected=replay_expected,
                    )

                    if replay_complete:
                        print("Historical Eagle replay is now complete.")

                    if event_result.status is not EventProcessStatus.ACCEPTED:
                        print("Duplicate/out-of-sequence replay event stopped.")
                    elif message.signal_id in missed_eagle_signal_ids:
                        print(
                            "MISSED EAGLE TRADE REPLAY CONSUMED - "
                            "no BTS lifecycle mutation and no broker order."
                        )
                    elif message.message_type not in {"fund.entry", "fund.exit"}:
                        print("Lifecycle type ignored.")
                    else:
                        adapt_result = adapter.adapt(message)
                        print(f"Adapter status: {adapt_result.status.value}")

                        if adapt_result.status is EagleTradeAdaptStatus.IGNORED_SYMBOL:
                            non_btc_events_ignored += 1
                            print("Non-BTC instrument ignored.")
                        elif (
                            adapt_result.status
                            is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
                        ):
                            non_btc_events_ignored += 1
                            print("Unknown/non-BTC exit ignored.")
                        elif adapt_result.status is EagleTradeAdaptStatus.ADAPTED:
                            btc_events_adapted += 1
                            normalized_event = adapt_result.event
                            if normalized_event is None:
                                raise RuntimeError(
                                    "Adapted Eagle event contained no event."
                                )

                            print(
                                "Normalized intent: "
                                f"{normalized_event.payload['intent']}"
                            )

                            replay_decision = coordinator.process_event(
                                normalized_event
                            )
                            print(
                                "Replay trade decision: "
                                f"{replay_decision.reason}"
                            )

                            if replay_decision.approved:
                                approved_decisions += 1
                            else:
                                rejected_decisions += 1

                            print(
                                "REPLAY HARD STOP - historical event cannot "
                                "reach IB execution."
                            )

                # -------------------------------------------------------------
                # LIVE lifecycle event.
                # In unarmed mode: inspect only; do not persist or mutate.
                # In armed mode: durable processing and execution are allowed.
                # -------------------------------------------------------------
                else:
                    print(f"Lifecycle: {message.message_type}")
                    print(f"Seq:       {message.seq}")
                    print(f"Signal ID: {message.signal_id}")

                    if not post_replay_heartbeat_seen:
                        raise RuntimeError(
                            "Live Eagle lifecycle arrived before required "
                            "post-replay heartbeat."
                        )

                    if message.signal_id in missed_eagle_signal_ids:
                        print(
                            "MISSED EAGLE TRADE LIFECYCLE IGNORED"
                        )
                        print(
                            f"Signal ID: {message.signal_id}"
                        )
                        print(
                            f"Lifecycle: {message.message_type}"
                        )
                        print(
                            "BTS never entered this Eagle trade; "
                            "NO broker order will be submitted."
                        )

                        if armed:
                            missed_event_result = (
                                event_processor.process(
                                    message
                                )
                            )
                            print(
                                "Event status: "
                                f"{missed_event_result.status.value}"
                            )
                        else:
                            print(
                                "OBSERVE MODE - missed lifecycle event "
                                "was not persisted."
                            )

                        if message.message_type == "fund.exit":
                            missed_eagle_signal_ids.discard(
                                message.signal_id
                            )
                            print(
                                "Missed Eagle trade is now closed; "
                                "BTS remains flat and ready for the "
                                "next fresh BTC fund.entry."
                            )

                        continue

                    # Adapter is non-executing. It lets us identify BTC intent
                    # before deciding whether armed durable processing is allowed.
                    adapt_result = adapter.adapt(message)
                    print(f"Adapter status: {adapt_result.status.value}")

                    if adapt_result.status is EagleTradeAdaptStatus.IGNORED_SYMBOL:
                        non_btc_events_ignored += 1
                        print("Non-BTC instrument ignored.")

                        if armed:
                            event_processor.process(message)
                        else:
                            print(
                                "OBSERVE MODE - non-BTC lifecycle event was not "
                                "persisted; durable cursor unchanged."
                            )
                        continue

                    if (
                        adapt_result.status
                        is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
                    ):
                        non_btc_events_ignored += 1
                        print("Unknown/non-BTC exit ignored.")

                        if armed:
                            event_processor.process(message)
                        else:
                            print(
                                "OBSERVE MODE - unknown exit was not persisted; "
                                "durable cursor unchanged."
                            )
                        continue

                    if adapt_result.status is not EagleTradeAdaptStatus.ADAPTED:
                        raise RuntimeError(
                            f"Unsupported EagleTradeAdaptStatus: "
                            f"{adapt_result.status!r}."
                        )

                    btc_events_adapted += 1
                    normalized_event = adapt_result.event
                    if normalized_event is None:
                        raise RuntimeError("Adapted Eagle event contained no event.")

                    print(
                        "Normalized intent: "
                        f"{normalized_event.payload['intent']}"
                    )

                    intended_intent = TradeIntent(
                        normalized_event.payload["intent"]
                    )

                    is_closing_intent = intended_intent in {
                        TradeIntent.SELL_TO_CLOSE,
                        TradeIntent.BUY_TO_CLOSE,
                    }

                    # ---------------------------------------------------------
                    # EXIT-OBLIGATION DURABILITY BOUNDARY
                    # ---------------------------------------------------------
                    if armed and is_closing_intent:
                        require_execution_state_clear(
                            execution_ledger
                        )

                        event_result = event_processor.process(
                            message
                        )
                        print(f"Event status: {event_result.status.value}")

                        if event_result.status is not EventProcessStatus.ACCEPTED:
                            print(
                                "Duplicate/out-of-sequence live exit stopped "
                                "before execution reservation."
                            )
                            continue

                        prepared_decision = coordinator.prepare_event(
                            normalized_event
                        )

                        print(
                            "Trade preparation approved: "
                            f"{prepared_decision.approved}"
                        )
                        print(
                            "Trade preparation reason:   "
                            f"{prepared_decision.reason}"
                        )

                        if not prepared_decision.approved:
                            rejected_decisions += 1
                            print(
                                "Trade preparation rejected; "
                                "durable lifecycle was NOT mutated."
                            )
                            continue

                        trade_request = prepared_decision.trade_request

                        if trade_request is None:
                            raise RuntimeError(
                                "Approved prepared decision had no TradeRequest."
                            )

                        execution_client.reserve_execution(
                            trade_request
                        )

                        print(
                            "Exit execution obligation durably RESERVED "
                            "before broker refresh."
                        )
                        print("Broker order has NOT been submitted.")

                        refresh_position_snapshot(
                            app=app,
                            manager=manager,
                            broker_client=broker_client,
                        )

                        broker_position, open_signals = (
                            reconcile_broker_and_lifecycle(
                                broker_client=broker_client,
                                lifecycle_database_path=lifecycle_database_path,
                                expected_local_symbol=execution_config.local_symbol,
                                expected_quantity=execution_config.quantity,
                            )
                        )

                        validate_trade_request_against_position(
                            trade_request=trade_request,
                            broker_position=broker_position,
                            open_signals=open_signals,
                            expected_quantity=execution_config.quantity,
                        )

                        risk_decision = risk_manager.evaluate(
                            trade_request,
                            current_position=broker_position,
                        )

                        print(f"Risk approved: {risk_decision.approved}")
                        print(
                            "Projected position: "
                            f"{risk_decision.projected_position}"
                        )

                        if not risk_decision.approved:
                            rejected_decisions += 1
                            raise RuntimeError(
                                "RiskManager rejected RESERVED live Eagle close "
                                "TradeRequest BEFORE durable lifecycle mutation: "
                                f"{risk_decision.reason}"
                            )

                        expected_position = expected_position_after_trade(
                            trade_request
                        )

                        if risk_decision.projected_position != expected_position:
                            raise RuntimeError(
                                "Risk projected position does not match "
                                "expected trade result."
                            )

                        readiness = IBTradingReadiness(
                            api_ready=app.api_ready,
                            order_id_allocator=app.order_id_allocator,
                            broker_client=broker_client,
                            trading_controls=trading_controls,
                            kill_switch=kill_switch,
                        )

                        readiness_result = readiness.require_ready(
                            positions_reconciled=True,
                            execution_state_clear=True,
                        )

                        print(f"IB readiness passed: {readiness_result.ready}")

                        broker_order_id = app.order_id_allocator.allocate()

                        print()
                        print("=" * 72)
                        print("LIVE PAPER ORDER AUTHORIZED")
                        print("=" * 72)
                        print(f"Eagle event: {trade_request.event_id}")
                        print(f"Signal ID:   {trade_request.signal_id}")
                        print(f"Intent:      {trade_request.intent.value}")
                        print(f"Quantity:    {execution_config.quantity} MBT")
                        print(f"IB order ID: {broker_order_id}")
                        print("=" * 72)

                        submission = execution_client.submit_reserved(
                            trade_request,
                            contract_month=execution_config.contract_month,
                            broker_order_id=broker_order_id,
                        )
                        broker_submissions += 1

                        expected_action = expected_ib_action(trade_request)
                        if submission.package.order.action != expected_action:
                            raise RuntimeError(
                                "IB order action does not match TradeRequest."
                            )

                        if (
                            submission.package.order.totalQuantity
                            != execution_config.quantity
                        ):
                            raise RuntimeError(
                                "IB order quantity does not match approved runtime quantity."
                            )

                        final_record = wait_for_execution_resolution(
                            execution_ledger=execution_ledger,
                            event_id=trade_request.event_id,
                            kill_switch=kill_switch,
                            timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
                        )

                        if final_record.status is not ExecutionStatus.FILLED:
                            raise RuntimeError(
                                "Paper order did not reach FILLED. "
                                f"Status: {final_record.status.value}. "
                                f"Reason: {final_record.reason}"
                            )

                        filled_orders += 1

                        # Broker has confirmed the close FILLED.
                        # Only now may BTS commit the durable lifecycle close.
                        decision = coordinator.commit_request(
                            trade_request
                        )

                        print(f"Trade decision approved: {decision.approved}")
                        print(f"Trade decision reason:   {decision.reason}")

                        if not decision.approved:
                            raise RuntimeError(
                                "Broker close FILLED but durable lifecycle "
                                "commit was rejected."
                            )

                        approved_decisions += 1

                        committed_trade_request = decision.trade_request

                        if committed_trade_request is None:
                            raise RuntimeError(
                                "Approved lifecycle commit had no TradeRequest."
                            )

                        if committed_trade_request != trade_request:
                            raise RuntimeError(
                                "Committed TradeRequest does not match "
                                "the filled TradeRequest."
                            )

                        post_decision_open_signals = load_durable_open_signals(
                            lifecycle_database_path
                        )

                        if post_decision_open_signals:
                            raise RuntimeError(
                                "Filled close did not leave durable lifecycle flat."
                            )

                        refresh_position_snapshot(
                            app=app,
                            manager=manager,
                            broker_client=broker_client,
                        )

                        reconciled_position, reconciled_open_signals = (
                            reconcile_broker_and_lifecycle(
                                broker_client=broker_client,
                                lifecycle_database_path=lifecycle_database_path,
                                expected_local_symbol=execution_config.local_symbol,
                                expected_quantity=execution_config.quantity,
                            )
                        )

                        if reconciled_position != expected_position:
                            raise RuntimeError(
                                "Post-fill broker position does not match "
                                "expected position."
                            )

                        print()
                        print("PAPER ORDER FILLED AND RECONCILED.")
                        print(f"Current MBT position: {reconciled_position}")
                        print(
                            "Durable open signals: "
                            f"{len(reconciled_open_signals)}"
                        )

                        continue

                    refresh_position_snapshot(
                        app=app,
                        manager=manager,
                        broker_client=broker_client,
                    )

                    broker_position, open_signals = reconcile_broker_and_lifecycle(
                        broker_client=broker_client,
                        lifecycle_database_path=lifecycle_database_path,
                        expected_local_symbol=execution_config.local_symbol,
                        expected_quantity=execution_config.quantity,
                    )

                    require_execution_state_clear(execution_ledger)

                    if intended_intent in {
                        TradeIntent.BUY_TO_OPEN,
                        TradeIntent.SELL_TO_OPEN,
                    }:
                        if broker_position != 0 or open_signals:
                            if armed:
                                event_result = event_processor.process(message)
                                print(f"Event status: {event_result.status.value}")

                                if (
                                    event_result.status
                                    is not EventProcessStatus.ACCEPTED
                                ):
                                    print(
                                        "Duplicate/out-of-sequence live event "
                                        "stopped before second-entry skip handling."
                                    )
                                    continue

                                rejected_decisions += 1

                                print("Opening signal skipped because BTS/broker is already positioned.")
                                print("No durable lifecycle was created for this signal.")
                                print("No broker order was submitted.")
                                print("Continuous trader remains active.")
                                continue

                            raise RuntimeError(
                                "Live opening signal blocked because BTS/broker "
                                "is not flat."
                            )
                    elif intended_intent in {
                        TradeIntent.SELL_TO_CLOSE,
                        TradeIntent.BUY_TO_CLOSE,
                    }:
                        if len(open_signals) != 1:
                            raise RuntimeError(
                                "Live closing signal requires exactly one durable "
                                "open signal."
                            )
                        if open_signals[0].signal_id != normalized_event.signal_id:
                            raise RuntimeError(
                                "Live closing signal does not match durable open signal."
                            )

                    # ---------------------------------
                    # OBSERVE-ONLY HARD BOUNDARY
                    #
                    # TradeCoordinator mutates durable
                    # signal lifecycle state. Therefore
                    # it must never be called unless
                    # continuous paper execution is
                    # explicitly armed.
                    # ---------------------------------
                    if not armed:
                        print("OBSERVE-ONLY HARD STOP.")
                        print(
                            "Live BTC signal validated through pre-mutation safety "
                            "checks."
                        )
                        print("TradeCoordinator was NOT called.")
                        print("Durable lifecycle was NOT mutated.")
                        print("RiskManager was NOT called.")
                        print("IBExecutionClient.submit was NOT called.")
                        print(
                            "Live lifecycle event was NOT persisted; durable cursor "
                            "was NOT advanced."
                        )
                        continue

                    # Armed path begins here. First durably accept the live frame.
                    event_result = event_processor.process(message)
                    print(f"Event status: {event_result.status.value}")

                    if event_result.status is not EventProcessStatus.ACCEPTED:
                        print(
                            "Duplicate/out-of-sequence live event stopped before "
                            "TradeCoordinator."
                        )
                        continue

                    prepared_decision = (
                        coordinator.prepare_event(
                            normalized_event
                        )
                    )

                    print(
                        "Trade preparation approved: "
                        f"{prepared_decision.approved}"
                    )
                    print(
                        "Trade preparation reason:   "
                        f"{prepared_decision.reason}"
                    )

                    if not prepared_decision.approved:
                        rejected_decisions += 1
                        print(
                            "Trade preparation rejected; "
                            "durable lifecycle was NOT mutated."
                        )
                        continue

                    trade_request = prepared_decision.trade_request

                    if trade_request is None:
                        raise RuntimeError(
                            "Approved prepared decision had no TradeRequest."
                        )

                    validate_trade_request_against_position(
                        trade_request=trade_request,
                        broker_position=broker_position,
                        open_signals=open_signals,
                        expected_quantity=execution_config.quantity,
                    )

                    risk_decision = risk_manager.evaluate(
                        trade_request,
                        current_position=broker_position,
                    )

                    print(f"Risk approved: {risk_decision.approved}")
                    print(
                        "Projected position: "
                        f"{risk_decision.projected_position}"
                    )

                    if not risk_decision.approved:
                        rejected_decisions += 1
                        raise RuntimeError(
                            "RiskManager rejected live Eagle TradeRequest "
                            "BEFORE durable lifecycle mutation: "
                            f"{risk_decision.reason}"
                        )

                    expected_position = expected_position_after_trade(
                        trade_request
                    )

                    if risk_decision.projected_position != expected_position:
                        raise RuntimeError(
                            "Risk projected position does not match "
                            "expected trade result."
                        )

                    readiness = IBTradingReadiness(
                        api_ready=app.api_ready,
                        order_id_allocator=app.order_id_allocator,
                        broker_client=broker_client,
                        trading_controls=trading_controls,
                        kill_switch=kill_switch,
                    )

                    readiness_result = readiness.require_ready(
                        positions_reconciled=True,
                        execution_state_clear=True,
                    )

                    print(
                        f"IB readiness passed: {readiness_result.ready}"
                    )

                    # All external safety checks have passed.
                    # Only now may BTS commit the durable Eagle lifecycle transition.
                    decision = coordinator.commit_request(
                        trade_request
                    )

                    print(f"Trade decision approved: {decision.approved}")
                    print(f"Trade decision reason:   {decision.reason}")

                    if not decision.approved:
                        rejected_decisions += 1
                        print(
                            "Lifecycle commit rejected; "
                            "no broker submission."
                        )
                        continue

                    approved_decisions += 1

                    committed_trade_request = decision.trade_request

                    if committed_trade_request is None:
                        raise RuntimeError(
                            "Approved lifecycle commit had no TradeRequest."
                        )

                    if committed_trade_request != trade_request:
                        raise RuntimeError(
                            "Committed TradeRequest does not match "
                            "the risk-approved TradeRequest."
                        )

                    post_decision_open_signals = load_durable_open_signals(
                        lifecycle_database_path
                    )

                    if expected_position == 0:
                        if post_decision_open_signals:
                            raise RuntimeError(
                                "Close decision did not leave durable lifecycle flat."
                            )
                    else:
                        if len(post_decision_open_signals) != 1:
                            raise RuntimeError(
                                "Open decision did not create exactly one durable "
                                "open signal."
                            )

                        expected_state = (
                            SignalLifecycleState.LONG_OPEN
                            if expected_position > 0
                            else SignalLifecycleState.SHORT_OPEN
                        )

                        durable_open = post_decision_open_signals[0]

                        if (
                            durable_open.signal_id != trade_request.signal_id
                            or durable_open.state is not expected_state
                        ):
                            raise RuntimeError(
                                "Post-decision durable lifecycle does not match "
                                "the TradeRequest."
                            )

                    broker_order_id = app.order_id_allocator.allocate()

                    print()
                    print("=" * 72)
                    print("LIVE PAPER ORDER AUTHORIZED")
                    print("=" * 72)
                    print(f"Eagle event: {trade_request.event_id}")
                    print(f"Signal ID:   {trade_request.signal_id}")
                    print(f"Intent:      {trade_request.intent.value}")
                    print(f"Quantity:    {execution_config.quantity} MBT")
                    print(f"IB order ID: {broker_order_id}")
                    print("=" * 72)

                    submission = execution_client.submit(
                        trade_request,
                        contract_month=execution_config.contract_month,
                        broker_order_id=broker_order_id,
                    )
                    broker_submissions += 1

                    expected_action = expected_ib_action(trade_request)
                    if submission.package.order.action != expected_action:
                        raise RuntimeError(
                            "IB order action does not match TradeRequest."
                        )

                    if (
                        submission.package.order.totalQuantity
                        != execution_config.quantity
                    ):
                        raise RuntimeError(
                            "IB order quantity does not match approved runtime quantity."
                        )

                    final_record = wait_for_execution_resolution(
                        execution_ledger=execution_ledger,
                        event_id=trade_request.event_id,
                        kill_switch=kill_switch,
                        timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
                    )

                    if final_record.status is not ExecutionStatus.FILLED:
                        raise RuntimeError(
                            "Paper order did not reach FILLED. "
                            f"Status: {final_record.status.value}. "
                            f"Reason: {final_record.reason}"
                        )

                    filled_orders += 1

                    refresh_position_snapshot(
                        app=app,
                        manager=manager,
                        broker_client=broker_client,
                    )

                    reconciled_position, reconciled_open_signals = (
                        reconcile_broker_and_lifecycle(
                            broker_client=broker_client,
                            lifecycle_database_path=lifecycle_database_path,
                            expected_local_symbol=execution_config.local_symbol,
                            expected_quantity=execution_config.quantity,
                        )
                    )

                    if reconciled_position != expected_position:
                        raise RuntimeError(
                            "Post-fill broker position does not match expected position."
                        )

                    print()
                    print("PAPER ORDER FILLED AND RECONCILED.")
                    print(f"Current MBT position: {reconciled_position}")
                    print(
                        "Durable open signals: "
                        f"{len(reconciled_open_signals)}"
                    )

            else:
                raise RuntimeError(
                    f"Unsupported Eagle message: {type(message).__name__}"
                )

            if max_messages > 0 and messages_observed >= max_messages:
                print()
                print("Configured message limit reached.")
                break

        refresh_position_snapshot(
            app=app,
            manager=manager,
            broker_client=broker_client,
        )

        final_mbt_position, final_open_signals = reconcile_broker_and_lifecycle(
            broker_client=broker_client,
            lifecycle_database_path=lifecycle_database_path,
            expected_local_symbol=execution_config.local_symbol,
            expected_quantity=execution_config.quantity,
        )

        return ContinuousPaperResult(
            armed=armed,
            hello_received=hello_received,
            staging_confirmed=staging_confirmed,
            replay_expected=replay_expected,
            replay_processed=replay_processed,
            replay_complete=replay_complete,
            post_replay_heartbeat_seen=post_replay_heartbeat_seen,
            heartbeats=heartbeats,
            lifecycle_events=lifecycle_events,
            btc_events_adapted=btc_events_adapted,
            non_btc_events_ignored=non_btc_events_ignored,
            approved_decisions=approved_decisions,
            rejected_decisions=rejected_decisions,
            broker_submissions=broker_submissions,
            filled_orders=filled_orders,
            final_mbt_position=final_mbt_position,
            durable_open_signal_count=len(final_open_signals),
            kill_switch_active=kill_switch.active,
            final_eagle_cursor=event_store.get_last_seq(),
        )

    finally:
        if app.position_request_active and app.isConnected():
            app.cancel_position_updates()
        manager.disconnect()


def print_result(result: ContinuousPaperResult) -> None:
    """Print a bounded continuous-runner result."""

    print()
    print()
    print("CONTINUOUS EAGLE -> IB PAPER SUMMARY")
    print("=" * 72)
    print(f"Execution armed:             {result.armed}")
    print(f"Eagle hello received:        {result.hello_received}")
    print(f"Eagle staging confirmed:     {result.staging_confirmed}")
    print(f"Replay expected:             {result.replay_expected}")
    print(f"Replay processed:            {result.replay_processed}")
    print(f"Replay complete:             {result.replay_complete}")
    print(f"Post-replay heartbeat:       {result.post_replay_heartbeat_seen}")
    print(f"Heartbeats:                  {result.heartbeats}")
    print(f"Lifecycle events:            {result.lifecycle_events}")
    print(f"BTC events adapted:          {result.btc_events_adapted}")
    print(f"Non-BTC events ignored:      {result.non_btc_events_ignored}")
    print(f"Approved decisions:          {result.approved_decisions}")
    print(f"Rejected decisions:          {result.rejected_decisions}")
    print(f"Broker submissions:          {result.broker_submissions}")
    print(f"Filled orders:               {result.filled_orders}")
    print(f"Final MBT position:          {result.final_mbt_position}")
    print(f"Durable open signals:        {result.durable_open_signal_count}")
    print(f"Kill switch active:          {result.kill_switch_active}")
    print(f"Final Eagle cursor:          {result.final_eagle_cursor}")
    print("=" * 72)


def parse_arguments() -> argparse.Namespace:
    """Parse continuous paper trader arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the continuous Eagle LIVE -> Interactive Brokers PAPER trader."
        )
    )

    parser.add_argument(
        ARMING_ARGUMENT,
        action="store_true",
        dest="confirm_continuous_paper",
        help="Explicitly authorize continuous TWS paper execution.",
    )
    parser.add_argument(
        RECOVERY_ARGUMENT,
        action="store_true",
        dest="recover_reserved_exit",
        help=(
            "Explicitly authorize recovery of one durable RESERVED "
            "MBT closing execution after operator review."
        ),
    )
    parser.add_argument(
        "--contract-month",
        required=True,
        help="Approved IB MBT futures expiry in YYYYMM or YYYYMMDD form.",
    )
    parser.add_argument(
        "--local-symbol",
        required=True,
        help="Expected TWS local symbol for the approved MBT contract.",
    )
    parser.add_argument(
        "--quantity",
        required=True,
        type=int,
        help=(
            "Approved MBT order quantity. Must be between 1 and "
            f"{MAX_CONFIGURABLE_QUANTITY}."
        ),
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help="Stop after this many Eagle messages. Use 0 for continuous operation.",
    )
    parser.add_argument("--event-database", default=str(DEFAULT_EVENT_DATABASE))
    parser.add_argument(
        "--lifecycle-database", default=str(DEFAULT_LIFECYCLE_DATABASE)
    )
    parser.add_argument(
        "--execution-ledger", default=str(DEFAULT_EXECUTION_LEDGER)
    )

    return parser.parse_args()


def main() -> int:
    """Run the continuous paper trader."""

    arguments = parse_arguments()
    api_key = os.environ.get(EAGLE_API_KEY_ENVIRONMENT_VARIABLE)

    if not api_key:
        print("BTS_EAGLE_LIVE_API_KEY is not configured.")
        return 1

    armed = bool(arguments.confirm_continuous_paper)
    recover_reserved_exit = bool(arguments.recover_reserved_exit)

    try:
        execution_config = validate_runtime_execution_config(
            contract_month=arguments.contract_month,
            local_symbol=arguments.local_symbol,
            quantity=arguments.quantity,
        )
    except (TypeError, ValueError) as error:
        print()
        print("CONTINUOUS PAPER TRADER FAIL-CLOSED")
        print(f"Invalid execution configuration: {error}")
        return 1

    print()
    print("Starting continuous Eagle -> IB PAPER trader...")

    if armed:
        print("WARNING: CONTINUOUS TWS PAPER EXECUTION IS ARMED.")
    else:
        print("OBSERVE / REPLAY MODE.")
        print("No broker orders will be submitted.")

    try:
        result = asyncio.run(
            run_continuous_paper_trader(
                armed=armed,
                api_key=api_key,
                event_database_path=arguments.event_database,
                lifecycle_database_path=arguments.lifecycle_database,
                execution_ledger_path=arguments.execution_ledger,
                max_messages=arguments.max_messages,
                execution_config=execution_config,
                recover_reserved_exit=recover_reserved_exit,
            )
        )
    except KeyboardInterrupt:
        print()
        print("Continuous paper trader stopped by user.")
        return 0
    except Exception as error:
        print()
        print("CONTINUOUS PAPER TRADER FAIL-CLOSED")
        print(f"{type(error).__name__}: {error}")
        print()
        print("No additional orders will be submitted.")
        return 1

    print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())