"""Temporary live test of the heartbeat watchdog."""

import asyncio

from app.connection_health import ConnectionHealth
from app.heartbeat_watchdog import HeartbeatWatchdog


async def main() -> None:
    """Verify that the asynchronous watchdog fires."""

    health = ConnectionHealth(
        heartbeat_timeout_seconds=5
    )

    watchdog = HeartbeatWatchdog(
        health,
        check_interval_seconds=1.0,
        initial_heartbeat_timeout_seconds=5.0,
    )

    health.record_heartbeat()

    print("Heartbeat recorded.")
    print("Waiting for watchdog timeout...")

    async def on_timeout() -> None:
        print()
        print("WATCHDOG CALLBACK FIRED")
        print(
            "Heartbeat age:",
            health.heartbeat_age_seconds(),
        )

    await watchdog.monitor(
        on_timeout
    )

    print("Watchdog monitor exited.")


if __name__ == "__main__":
    asyncio.run(main())