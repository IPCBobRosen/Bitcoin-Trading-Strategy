"""Normal fake Eagle WebSocket server for BTS integration testing."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve


HOST = "localhost"
PORT = 8765
MESSAGE_DELAY_SECONDS = 2
SERVER_LAST_SEQ = 5


def current_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat()


def get_requested_since_seq(
    websocket: ServerConnection,
) -> int:
    """Read since_seq from the incoming WebSocket request URI."""

    request_path = websocket.request.path

    parsed_path = urlsplit(
        request_path
    )

    query_parameters = parse_qs(
        parsed_path.query
    )

    raw_values = query_parameters.get(
        "since_seq"
    )

    if not raw_values:
        return 0

    raw_since_seq = raw_values[-1]

    try:
        since_seq = int(
            raw_since_seq
        )

    except ValueError as error:
        raise ValueError(
            "'since_seq' must be a non-negative integer."
        ) from error

    if since_seq < 0:
        raise ValueError(
            "'since_seq' must be a non-negative integer."
        )

    return since_seq


def create_hello(
    *,
    since_seq: int,
    replay_count: int,
) -> dict[str, Any]:
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
        "last_seq": SERVER_LAST_SEQ,
        "since_seq": since_seq,
        "open_count": 0,
        "open": [],
        "open_by_channel": {
            "fund": [],
            "apollo": [],
            "hermes": [],
            "athena": [],
            "moab": [],
        },
        "replay_count": replay_count,
        "ts": current_timestamp(),
        "env": "staging",
    }


def create_heartbeat(
    *,
    seq: int,
) -> dict[str, Any]:
    """Create a realistic Eagle fund.heartbeat control frame."""

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


def create_test_messages() -> list[dict[str, Any]]:
    """Create the server's complete ordered Eagle message history."""

    return [
        create_lifecycle_event(
            message_type="fund.entry",
            seq=1,
            event_id="local-event-001",
            signal_id="local-signal-001",
            intent="BUY_TO_OPEN",
        ),
        create_heartbeat(
            seq=2,
        ),
        create_lifecycle_event(
            message_type="fund.entry",
            seq=3,
            event_id="local-event-003",
            signal_id="local-signal-003",
            intent="SELL_TO_OPEN",
        ),
        create_heartbeat(
            seq=4,
        ),
        create_lifecycle_event(
            message_type="fund.exit",
            seq=5,
            event_id="local-event-005",
            signal_id="local-signal-001",
            intent="SELL_TO_CLOSE",
        ),
    ]


def message_seq(
    message: dict[str, Any],
) -> int:
    """Return the sequence number from an Eagle test message."""

    seq = message["seq"]

    if not isinstance(seq, int):
        raise ValueError(
            "Test message 'seq' must be an integer."
        )

    return seq


def is_lifecycle_message(
    message: dict[str, Any],
) -> bool:
    """Return True when a message is an Eagle lifecycle event."""

    message_type = message.get(
        "type"
    )

    return message_type != "fund.heartbeat"


async def send_message(
    websocket: ServerConnection,
    message: dict[str, Any],
) -> None:
    """Send one Eagle JSON message."""

    await websocket.send(
        json.dumps(message)
    )

    message_type = message["type"]
    seq = message["seq"]

    if message_type == "fund.heartbeat":
        print(
            f"Sent seq {seq}: fund.heartbeat"
        )

    else:
        intent = message["payload"]["intent"]

        print(
            f"Sent seq {seq}: "
            f"{message_type} / {intent}"
        )

    await asyncio.sleep(
        MESSAGE_DELAY_SECONDS
    )


async def handle_client(
    websocket: ServerConnection,
) -> None:
    """Send hello followed by messages newer than since_seq."""

    print("BTS client connected.")

    try:
        requested_since_seq = (
            get_requested_since_seq(
                websocket
            )
        )

        print(
            f"Client requested since_seq="
            f"{requested_since_seq}"
        )

        all_messages = (
            create_test_messages()
        )

        replay_messages = [
            message
            for message in all_messages
            if message_seq(message)
            > requested_since_seq
        ]

        replay_lifecycle_count = sum(
            1
            for message in replay_messages
            if is_lifecycle_message(
                message
            )
        )

        hello = create_hello(
            since_seq=requested_since_seq,
            replay_count=replay_lifecycle_count,
        )

        await websocket.send(
            json.dumps(hello)
        )

        print(
            "Sent fund.hello control frame."
        )

        print(
            f"Hello since_seq   : "
            f"{requested_since_seq}"
        )

        print(
            f"Hello last_seq    : "
            f"{SERVER_LAST_SEQ}"
        )

        print(
            f"Hello replay_count: "
            f"{replay_lifecycle_count}"
        )

        await asyncio.sleep(
            MESSAGE_DELAY_SECONDS
        )

        for message in replay_messages:
            await send_message(
                websocket,
                message,
            )

        print()
        print(
            "All requested Eagle messages sent."
        )

        print(
            "Closing this client connection."
        )

    except ConnectionError as error:
        print()
        print(
            f"BTS client connection ended early: "
            f"{error}"
        )

    except ValueError as error:
        print()
        print(
            f"Invalid client reconnect request: "
            f"{error}"
        )


async def main() -> None:
    """Start the normal fake Eagle WebSocket server."""

    print("=" * 60)
    print("Fake Eagle WebSocket Server")
    print("=" * 60)

    print(
        f"Listening at ws://{HOST}:{PORT}"
    )

    print(
        "Mode: NORMAL / REPLAY-AWARE"
    )

    print(
        "Waiting for the BTS client..."
    )

    print(
        "Press Ctrl+C to stop the server."
    )

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
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        print()
        print(
            "Fake Eagle server stopped."
        )