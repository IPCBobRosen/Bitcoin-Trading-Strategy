"""Read-only Interactive Brokers paper-account connection test.

This harness verifies the BTS-to-TWS API connection without
providing an order-submission path.

Expected environment:

    TWS paper account
    Host: 127.0.0.1
    Port: 7497
    Client ID: 1

For the first real connection test, TWS should have Read-Only API
enabled as an additional independent safety barrier.
"""

from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from collections.abc import Callable

from app.execution_ledger import ExecutionLedger
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.kill_switch import KillSwitch


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7497
DEFAULT_CLIENT_ID = 1
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class IBPaperConnectionResult:
    """Immutable result of one read-only TWS paper test."""

    host: str
    port: int
    client_id: int
    api_ready: bool
    next_valid_order_id: int | None
    order_id_allocator_initialized: bool
    position_snapshot_complete: bool
    position_count: int
    kill_switch_active: bool

    @property
    def successful(self) -> bool:
        """Return True when all read-only connection checks passed."""

        return (
            self.api_ready
            and self.next_valid_order_id is not None
            and self.order_id_allocator_initialized
            and self.position_snapshot_complete
            and not self.kill_switch_active
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic_function: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until a condition becomes True or raise TimeoutError."""

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

    if not callable(
        sleep_function
    ):
        raise TypeError(
            "'sleep_function' must be callable."
        )

    if not callable(
        monotonic_function
    ):
        raise TypeError(
            "'monotonic_function' must be callable."
        )

    deadline = (
        monotonic_function()
        + float(
            timeout_seconds
        )
    )

    while not condition():
        if (
            monotonic_function()
            >= deadline
        ):
            raise TimeoutError(
                f"Timed out waiting for "
                f"{description.strip()}."
            )

        sleep_function(
            float(
                poll_interval_seconds
            )
        )


def run_read_only_paper_connection_test(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    client_id: int = DEFAULT_CLIENT_ID,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ledger_path: str | Path | None = None,
) -> IBPaperConnectionResult:
    """Run the real BTS-to-TWS read-only connection test."""

    if ledger_path is None:
        with tempfile.TemporaryDirectory(
            prefix="bts_ib_paper_test_"
        ) as temporary_directory:
            temporary_ledger_path = (
                Path(
                    temporary_directory
                )
                / "execution_ledger.db"
            )

            return _run_with_ledger(
                host=host,
                port=port,
                client_id=client_id,
                timeout_seconds=timeout_seconds,
                ledger_path=temporary_ledger_path,
            )

    return _run_with_ledger(
        host=host,
        port=port,
        client_id=client_id,
        timeout_seconds=timeout_seconds,
        ledger_path=Path(
            ledger_path
        ),
    )


def _run_with_ledger(
    *,
    host: str,
    port: int,
    client_id: int,
    timeout_seconds: float,
    ledger_path: Path,
) -> IBPaperConnectionResult:
    """Execute the connection test using one temporary ledger."""

    broker_client = IBBrokerClient()

    execution_ledger = ExecutionLedger(
        ledger_path
    )

    kill_switch = KillSwitch()

    app = IBApiPositionApp(
        broker_client,
        execution_ledger=execution_ledger,
        kill_switch=kill_switch,
    )

    manager = IBConnectionManager(
        app,
        host=host,
        port=port,
        client_id=client_id,
        connection_timeout_seconds=timeout_seconds,
    )

    try:
        manager.connect()

        wait_until(
            lambda: app.api_ready.ready,
            description="IB nextValidId handshake",
            timeout_seconds=timeout_seconds,
        )

        if kill_switch.active:
            raise RuntimeError(
                "IB kill switch activated during "
                "connection handshake: "
                f"{kill_switch.reason}"
            )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="IB position snapshot completion",
            timeout_seconds=timeout_seconds,
        )

        if kill_switch.active:
            raise RuntimeError(
                "IB kill switch activated during "
                "position snapshot: "
                f"{kill_switch.reason}"
            )

        positions = (
            broker_client.get_raw_positions()
        )

        return IBPaperConnectionResult(
            host=manager.host,
            port=manager.port,
            client_id=manager.client_id,
            api_ready=app.api_ready.ready,
            next_valid_order_id=(
                app.api_ready.next_valid_order_id
            ),
            order_id_allocator_initialized=(
                app.order_id_allocator.initialized
            ),
            position_snapshot_complete=(
                broker_client.snapshot_complete
            ),
            position_count=len(
                positions
            ),
            kill_switch_active=kill_switch.active,
        )

    finally:
        if (
            app.position_request_active
            and app.isConnected()
        ):
            app.cancel_position_updates()

        manager.disconnect()


def print_result(
    result: IBPaperConnectionResult,
) -> None:
    """Print a human-readable read-only test result."""

    if not isinstance(
        result,
        IBPaperConnectionResult,
    ):
        raise TypeError(
            "'result' must be an IBPaperConnectionResult."
        )

    print()
    print(
        "BTS / IB PAPER READ-ONLY CONNECTION TEST"
    )
    print(
        "========================================"
    )
    print(
        f"Host:                     {result.host}"
    )
    print(
        f"Port:                     {result.port}"
    )
    print(
        f"Client ID:                {result.client_id}"
    )
    print(
        f"API handshake ready:      {result.api_ready}"
    )
    print(
        "Next valid order ID:      "
        f"{result.next_valid_order_id}"
    )
    print(
        "Order-ID allocator ready: "
        f"{result.order_id_allocator_initialized}"
    )
    print(
        "Position snapshot done:   "
        f"{result.position_snapshot_complete}"
    )
    print(
        f"Open positions returned:  {result.position_count}"
    )
    print(
        f"Kill switch active:       {result.kill_switch_active}"
    )
    print(
        "Order submission path:    NOT PRESENT"
    )
    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - read-only IB connection is healthy."
        )

    else:
        print(
            "RESULT: FAIL - one or more connection checks failed."
        )

    print()


def main() -> int:
    """Run the first real BTS/TWS paper connection test."""

    print()
    print(
        "Starting READ-ONLY TWS paper connection test..."
    )
    print(
        "No order-submission code exists in this harness."
    )
    print()

    try:
        result = (
            run_read_only_paper_connection_test()
        )

    except Exception as error:
        print(
            "RESULT: FAIL"
        )
        print(
            f"{type(error).__name__}: {error}"
        )

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