"""Fake Eagle server for silent heartbeat-failure integration testing."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.server import ServerConnection, serve


HOST = "localhost"
PORT = 8765
MESSAGE_DELAY_SECONDS = 2

# Keep the socket open long enough for BTS to detect that
# application-level Eagle heartbeats have stopped.
SILENT_HOLD_SECONDS = 60


def current_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat()


def create_hello() -> dict[str, Any]:
    """Create a realistic Eagle fund.hello control frame."""

    return {
        "type": "fund.hello",
        "contract": "1.2.0",
        "version": "1.2.0",
        "capabilities": [
            "size_mult",
            "fund.add",
            "funding_rate",
            "p14_disable",
        ],
        "flags": {
            "p14_disabled": True,
            "size_mult_enabled": False,
            "pyramid_enabled": False,
        },
        "last_seq": 2,
        "since_seq": 0,
        "open_count": 0,
        "open": [],
        "open_by_channel": {
            "fund": [],
            "apollo": [],
            "hermes": [],
            "athena": [],
            "moab": [],
        },
        "replay_count": 0,
        "ts": current_timestamp(),
        "env": "staging",
    }


def create_heartbeat(
    *,
    seq: int,
) -> dict[str, Any]:
    """Create one Eagle fund.heartbeat control frame."""

    return {
        "type": "fund.heartbeat",
        "seq": seq,
        "open_count": 0,
        "open_count_by_channel": {
            "fund": 0,
            "apollo": 0,
            "hermes": 0,
            "athena": 0,
            "moab": 0,
        },
    }


def create_lifecycle_event(
    *,
    message_type: str,
    seq: int,
    event_id: str,
    signal_id: str,
    intent: str,
) -> dict[str, Any]:
    """Create one realistic Eagle lifecycle-event message."""

    return {
        "type": message_type,
        "seq": seq,
        "event_id": event_id,
        "signal_id": signal_id,
        "ts": current_timestamp(),
        "env": "staging",
        "payload": {
            "intent": intent,
        },
    }


async def send_message(
    websocket: ServerConnection,
    message: dict[str, Any],
    description: str,
) -> None:
    """Send one JSON message to the connected BTS client."""

    await websocket.send(
        json.dumps(message)
    )

    print(description)

    await asyncio.sleep(
        MESSAGE_DELAY_SECONDS
    )


async def handle_client(
    websocket: ServerConnection,
) -> None:
    """Send one heartbeat, then intentionally become silent."""

    print("BTS client connected.")

    try:
        hello = create_hello()

        await send_message(
            websocket,
            hello,
            "Sent fund.hello control frame.",
        )

        entry_event = create_lifecycle_event(
            message_type="fund.entry",
            seq=1,
            event_id="timeout-event-001",
            signal_id="timeout-signal-001",
            intent="BUY_TO_OPEN",
        )

        await send_message(
            websocket,
            entry_event,
            "Sent seq 1: fund.entry / BUY_TO_OPEN",
        )

        heartbeat = create_heartbeat(
            seq=2,
        )

        await websocket.send(
            json.dumps(heartbeat)
        )

        print("Sent seq 2: fund.heartbeat")

        print()
        print("=" * 60)
        print("SIMULATING SILENT EAGLE HEARTBEAT FAILURE")
        print("=" * 60)
        print("The WebSocket will remain open.")
        print("No more fund.heartbeat frames will be sent.")
        print(
            f"Silent hold period: "
            f"{SILENT_HOLD_SECONDS} seconds."
        )
        print("=" * 60)
        print()

        await asyncio.sleep(
            SILENT_HOLD_SECONDS
        )

        print()
        print("Silent test period completed.")

    except ConnectionError as error:
        print()
        print(
            "BTS client ended the connection "
            f"during the silent period: {error}"
        )


async def main() -> None:
    """Start the heartbeat-failure fake Eagle server."""

    print("=" * 60)
    print("Fake Eagle Heartbeat Failure Server")
    print("=" * 60)
    print(f"Listening at ws://{HOST}:{PORT}")
    print("Mode: SILENT HEARTBEAT FAILURE")
    print("Waiting for the BTS client...")
    print("Press Ctrl+C to stop the server.")

    async with serve(
        handle_client,
        HOST,
        PORT,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_048_576,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print()
        print("Fake Eagle heartbeat failure server stopped.")