"""Read-only IB paper disconnect and recovery test for BTS.

This harness:

1. Connects to TWS paper.
2. Receives nextValidId.
3. Completes a position snapshot.
4. Requires the paper account to be flat.
5. Verifies BTS trading readiness.
6. Deliberately disconnects locally.
7. Proves BTS readiness is revoked and execution is blocked.
8. Reconnects.
9. Receives a fresh nextValidId.
10. Completes a fresh position snapshot.
11. Verifies the account is still flat.
12. Verifies trading readiness is restored.

No order-submission component is imported or used.
"""

from dataclasses import dataclass
from pathlib import Path
import time
from collections.abc import Callable

from app.execution_ledger import ExecutionLedger
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.ib_trading_readiness import (
    IBReadinessFailure,
    IBTradingReadiness,
    IBTradingReadinessSnapshot,
)
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls


HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 1

CONNECTION_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class IBDisconnectRecoveryResult:
    """Immutable result of one disconnect/reconnect test."""

    initial_api_ready: bool
    initial_next_valid_order_id: int | None
    initial_position_count: int
    initial_readiness: bool

    disconnected_socket: bool
    disconnected_api_ready: bool
    disconnected_readiness: bool
    execution_blocked_while_disconnected: bool

    reconnect_api_ready: bool
    reconnect_next_valid_order_id: int | None
    reconnect_position_count: int
    reconnect_readiness: bool

    kill_switch_active: bool

    @property
    def successful(self) -> bool:
        """Return True when the complete recovery test passed."""

        return (
            self.initial_api_ready
            and self.initial_next_valid_order_id is not None
            and self.initial_position_count == 0
            and self.initial_readiness
            and self.disconnected_socket
            and not self.disconnected_api_ready
            and not self.disconnected_readiness
            and self.execution_blocked_while_disconnected
            and self.reconnect_api_ready
            and self.reconnect_next_valid_order_id is not None
            and self.reconnect_position_count == 0
            and self.reconnect_readiness
            and not self.kill_switch_active
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float = CONNECTION_TIMEOUT_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
) -> None:
    """Wait until one asynchronous IB condition becomes true."""

    if not callable(
        condition
    ):
        raise TypeError(
            "'condition' must be callable."
        )

    if (
        not isinstance(description, str)
        or not description.strip()
    ):
        raise ValueError(
            "'description' must be a non-empty string."
        )

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(
            timeout_seconds,
            (int, float),
        )
        or timeout_seconds <= 0
    ):
        raise ValueError(
            "'timeout_seconds' must be positive."
        )

    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(
            poll_interval_seconds,
            (int, float),
        )
        or poll_interval_seconds <= 0
    ):
        raise ValueError(
            "'poll_interval_seconds' must be positive."
        )

    deadline = (
        time.monotonic()
        + float(
            timeout_seconds
        )
    )

    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for "
                f"{description.strip()}."
            )

        time.sleep(
            float(
                poll_interval_seconds
            )
        )


def require_flat_account(
    broker_client: IBBrokerClient,
) -> int:
    """Require a completed broker snapshot containing no positions."""

    if not isinstance(
        broker_client,
        IBBrokerClient,
    ):
        raise TypeError(
            "'broker_client' must be an IBBrokerClient."
        )

    positions = (
        broker_client.get_raw_positions()
    )

    if positions:
        raise RuntimeError(
            "Disconnect/recovery test requires "
            "the paper account to be completely flat."
        )

    return len(
        positions
    )


def require_execution_blocked_while_disconnected(
    readiness: IBTradingReadiness,
) -> IBTradingReadinessSnapshot:
    """Prove the execution-readiness gate fails after disconnect."""

    if not isinstance(
        readiness,
        IBTradingReadiness,
    ):
        raise TypeError(
            "'readiness' must be an IBTradingReadiness."
        )

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    if result.ready:
        raise RuntimeError(
            "Safety violation: BTS remained trading-ready "
            "after IB disconnect."
        )

    if (
        IBReadinessFailure.API_NOT_READY
        not in result.failures
    ):
        raise RuntimeError(
            "Disconnected BTS did not report ApiNotReady."
        )

    try:
        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=True,
        )

    except RuntimeError:
        return result

    raise RuntimeError(
        "Safety violation: mandatory readiness gate "
        "did not block execution while disconnected."
    )


def run_disconnect_recovery_test(
    *,
    ledger_path: str | Path,
) -> IBDisconnectRecoveryResult:
    """Run one real read-only TWS disconnect/reconnect cycle."""

    broker_client = IBBrokerClient()

    execution_ledger = ExecutionLedger(
        ledger_path
    )

    kill_switch = KillSwitch()

    trading_controls = TradingControls()

    trading_controls.resume()

    app = IBApiPositionApp(
        broker_client,
        execution_ledger=execution_ledger,
        kill_switch=kill_switch,
    )

    manager = IBConnectionManager(
        app,
        host=HOST,
        port=PORT,
        client_id=CLIENT_ID,
        connection_timeout_seconds=(
            CONNECTION_TIMEOUT_SECONDS
        ),
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

    try:
        # ---------------------------------------------------------
        # Initial connection
        # ---------------------------------------------------------

        manager.connect()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="initial IB position snapshot",
        )

        initial_position_count = (
            require_flat_account(
                broker_client
            )
        )

        initial_readiness_result = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        initial_next_valid_order_id = (
            app.api_ready.next_valid_order_id
        )

        # ---------------------------------------------------------
        # Deliberate local disconnect
        # ---------------------------------------------------------

        manager.disconnect()

        disconnected_socket = (
            not app.isConnected()
        )

        disconnected_api_ready = (
            app.api_ready.ready
        )

        disconnected_result = (
            require_execution_blocked_while_disconnected(
                readiness
            )
        )

        if kill_switch.active:
            raise RuntimeError(
                "Deliberate local disconnect unexpectedly "
                "activated the BTS kill switch: "
                f"{kill_switch.reason}"
            )

        # ---------------------------------------------------------
        # Reconnect
        # ---------------------------------------------------------

        manager.connect()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="reconnect IB position snapshot",
        )

        reconnect_position_count = (
            require_flat_account(
                broker_client
            )
        )

        reconnect_readiness_result = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        reconnect_next_valid_order_id = (
            app.api_ready.next_valid_order_id
        )

        return IBDisconnectRecoveryResult(
            initial_api_ready=True,
            initial_next_valid_order_id=(
                initial_next_valid_order_id
            ),
            initial_position_count=(
                initial_position_count
            ),
            initial_readiness=(
                initial_readiness_result.ready
            ),
            disconnected_socket=(
                disconnected_socket
            ),
            disconnected_api_ready=(
                disconnected_api_ready
            ),
            disconnected_readiness=(
                disconnected_result.ready
            ),
            execution_blocked_while_disconnected=True,
            reconnect_api_ready=(
                app.api_ready.ready
            ),
            reconnect_next_valid_order_id=(
                reconnect_next_valid_order_id
            ),
            reconnect_position_count=(
                reconnect_position_count
            ),
            reconnect_readiness=(
                reconnect_readiness_result.ready
            ),
            kill_switch_active=(
                kill_switch.active
            ),
        )

    finally:
        manager.disconnect()


def print_result(
    result: IBDisconnectRecoveryResult,
) -> None:
    """Print the disconnect/recovery test result."""

    if not isinstance(
        result,
        IBDisconnectRecoveryResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBDisconnectRecoveryResult."
        )

    print()
    print(
        "BTS / IB PAPER DISCONNECT RECOVERY TEST"
    )
    print(
        "========================================"
    )

    print(
        "Initial API ready:         "
        f"{result.initial_api_ready}"
    )
    print(
        "Initial nextValidId:       "
        f"{result.initial_next_valid_order_id}"
    )
    print(
        "Initial position count:    "
        f"{result.initial_position_count}"
    )
    print(
        "Initial readiness:         "
        f"{result.initial_readiness}"
    )

    print(
        "----------------------------------------"
    )

    print(
        "Socket disconnected:       "
        f"{result.disconnected_socket}"
    )
    print(
        "API ready after disconnect:"
        f" {result.disconnected_api_ready}"
    )
    print(
        "Trading ready disconnected:"
        f" {result.disconnected_readiness}"
    )
    print(
        "Execution blocked:         "
        f"{result.execution_blocked_while_disconnected}"
    )

    print(
        "----------------------------------------"
    )

    print(
        "Reconnect API ready:       "
        f"{result.reconnect_api_ready}"
    )
    print(
        "Reconnect nextValidId:     "
        f"{result.reconnect_next_valid_order_id}"
    )
    print(
        "Reconnect position count:  "
        f"{result.reconnect_position_count}"
    )
    print(
        "Reconnect readiness:       "
        f"{result.reconnect_readiness}"
    )
    print(
        "Kill switch active:        "
        f"{result.kill_switch_active}"
    )

    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - IB disconnect/recovery "
            "completed safely."
        )

    else:
        print(
            "RESULT: FAIL - disconnect/recovery "
            "validation failed."
        )

    print()


def main() -> int:
    """Run the real read-only IB disconnect/reconnect test."""

    ledger_path = (
        Path("data")
        / "ib_disconnect_recovery_test.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(
        "Starting IB paper disconnect/recovery test..."
    )
    print(
        "No order-submission path exists in this harness."
    )

    try:
        result = (
            run_disconnect_recovery_test(
                ledger_path=ledger_path
            )
        )

    except Exception as error:
        print()
        print(
            "RESULT: FAIL"
        )
        print(
            f"{type(error).__name__}: {error}"
        )
        print()

        return 1

    print_result(
        result
    )

    if result.successful:
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )