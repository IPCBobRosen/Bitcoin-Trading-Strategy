"""Fake Eagle server for real disconnect/reconnect integration testing.

This server deliberately creates a genuine WebSocket interruption.

Session 1:
    - accept BTS connection
    - read the requested since_seq
    - send fund.hello
    - send historical messages through sequence 3
    - deliberately close the WebSocket

Session 2:
    - accept the BTS reconnect
    - read the new durable since_seq
    - send fund.hello
    - replay only messages newer than that cursor
    - send one fresh heartbeat
    - hold briefly, then close normally

The protocol fields intentionally match scripts.fake_eagle_server.

This server contains no broker or Interactive Brokers code.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import (
    ServerConnection,
    serve,
)
from websockets.exceptions import ConnectionClosed


HOST = "localhost"
PORT = 8766

MESSAGE_DELAY_SECONDS = 1.0
POST_RECOVERY_HOLD_SECONDS = 3.0

HISTORY_LAST_SEQ = 5

# The first WebSocket session is deliberately interrupted
# after historical sequence 3 has been sent.
FIRST_SESSION_DISCONNECT_AFTER_SEQ = 3

INTENTIONAL_DISCONNECT_CODE = 1012
INTENTIONAL_DISCONNECT_REASON = (
    "Fake Eagle intentional integration-test disconnect."
)


def current_timestamp() -> str:
    """Return current UTC time in ISO-8601 format."""

    return datetime.now(
        timezone.utc
    ).isoformat()


def get_requested_since_seq(
    websocket: ServerConnection,
) -> int:
    """Read since_seq from the incoming WebSocket request."""

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
    last_seq: int,
    replay_count: int,
) -> dict[str, Any]:
    """Create one realistic Eagle fund.hello control frame."""

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
        "last_seq": last_seq,
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
    """Create one realistic Eagle heartbeat."""

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
    """Create one realistic Eagle lifecycle event."""

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


def create_test_messages() -> list[
    dict[str, Any]
]:
    """Create deterministic Eagle historical message stream."""

    return [
        create_lifecycle_event(
            message_type="fund.entry",
            seq=1,
            event_id="disconnect-event-001",
            signal_id="disconnect-signal-001",
            intent="BUY_TO_OPEN",
        ),
        create_heartbeat(
            seq=2,
        ),
        create_lifecycle_event(
            message_type="fund.entry",
            seq=3,
            event_id="disconnect-event-003",
            signal_id="disconnect-signal-003",
            intent="SELL_TO_OPEN",
        ),
        create_heartbeat(
            seq=4,
        ),
        create_lifecycle_event(
            message_type="fund.exit",
            seq=5,
            event_id="disconnect-event-005",
            signal_id="disconnect-signal-001",
            intent="SELL_TO_CLOSE",
        ),
    ]


def message_seq(
    message: dict[str, Any],
) -> int:
    """Return validated sequence number from test message."""

    seq = message["seq"]

    if (
        not isinstance(seq, int)
        or isinstance(seq, bool)
        or seq < 0
    ):
        raise ValueError(
            "Test message 'seq' must be a "
            "non-negative integer."
        )

    return seq


def is_lifecycle_message(
    message: dict[str, Any],
) -> bool:
    """Return True for Eagle entry/exit messages."""

    return (
        message.get("type")
        in {
            "fund.entry",
            "fund.exit",
        }
    )


def messages_after_seq(
    *,
    since_seq: int,
) -> list[dict[str, Any]]:
    """Return historical messages newer than since_seq."""

    if (
        not isinstance(since_seq, int)
        or isinstance(since_seq, bool)
        or since_seq < 0
    ):
        raise ValueError(
            "'since_seq' must be a non-negative integer."
        )

    return [
        message
        for message in create_test_messages()
        if message_seq(
            message
        )
        > since_seq
    ]


def lifecycle_replay_count(
    messages: list[
        dict[str, Any]
    ],
) -> int:
    """Return number of lifecycle messages in replay."""

    return sum(
        1
        for message in messages
        if is_lifecycle_message(
            message
        )
    )


async def send_message(
    websocket: ServerConnection,
    message: dict[str, Any],
) -> None:
    """Send and display one Eagle JSON message."""

    await websocket.send(
        json.dumps(
            message
        )
    )

    message_type = message["type"]
    seq = message["seq"]

    if (
        message_type
        == "fund.heartbeat"
    ):
        print(
            f"Sent seq {seq}: fund.heartbeat"
        )

    else:
        intent = (
            message["payload"]["intent"]
        )

        print(
            f"Sent seq {seq}: "
            f"{message_type} / {intent}"
        )

    await asyncio.sleep(
        MESSAGE_DELAY_SECONDS
    )


async def send_hello(
    websocket: ServerConnection,
    *,
    requested_since_seq: int,
    replay_messages: list[
        dict[str, Any]
    ],
) -> None:
    """Send reconnect-aware fund.hello."""

    current_server_last_seq = max(
        HISTORY_LAST_SEQ,
        requested_since_seq,
    )

    hello = create_hello(
        since_seq=requested_since_seq,
        last_seq=current_server_last_seq,
        replay_count=(
            lifecycle_replay_count(
                replay_messages
            )
        ),
    )

    await websocket.send(
        json.dumps(
            hello
        )
    )

    print(
        "Sent fund.hello control frame."
    )
    print(
        f"Hello since_seq    : "
        f"{requested_since_seq}"
    )
    print(
        f"Hello last_seq     : "
        f"{current_server_last_seq}"
    )
    print(
        f"Hello replay_count : "
        f"{hello['replay_count']}"
    )

    await asyncio.sleep(
        MESSAGE_DELAY_SECONDS
    )


async def run_first_session(
    websocket: ServerConnection,
    *,
    requested_since_seq: int,
) -> None:
    """Send partial replay and deliberately break connection."""

    all_replay_messages = (
        messages_after_seq(
            since_seq=requested_since_seq
        )
    )

    # The hello describes the complete replay Eagle has
    # available. BTS will therefore know replay is unfinished
    # when the socket disappears partway through delivery.
    await send_hello(
        websocket,
        requested_since_seq=(
            requested_since_seq
        ),
        replay_messages=(
            all_replay_messages
        ),
    )

    partial_messages = [
        message
        for message in all_replay_messages
        if (
            message_seq(
                message
            )
            <= FIRST_SESSION_DISCONNECT_AFTER_SEQ
        )
    ]

    for message in partial_messages:
        await send_message(
            websocket,
            message,
        )

    print()
    print("=" * 60)
    print(
        "INTENTIONAL EAGLE WEBSOCKET DISCONNECT"
    )
    print("=" * 60)
    print(
        "First session replay was deliberately "
        "interrupted."
    )
    print(
        f"Disconnect after seq "
        f"{FIRST_SESSION_DISCONNECT_AFTER_SEQ}."
    )
    print(
        "BTS should remain fail-closed and reconnect "
        "using its new durable cursor."
    )
    print("=" * 60)
    print()

    await websocket.close(
        code=INTENTIONAL_DISCONNECT_CODE,
        reason=INTENTIONAL_DISCONNECT_REASON,
    )


async def run_recovery_session(
    websocket: ServerConnection,
    *,
    requested_since_seq: int,
) -> None:
    """Replay remaining history and send fresh heartbeat."""

    replay_messages = (
        messages_after_seq(
            since_seq=requested_since_seq
        )
    )

    await send_hello(
        websocket,
        requested_since_seq=(
            requested_since_seq
        ),
        replay_messages=(
            replay_messages
        ),
    )

    for message in replay_messages:
        await send_message(
            websocket,
            message,
        )

    print()
    print(
        "Recovery replay delivery complete."
    )

    current_server_last_seq = max(
        HISTORY_LAST_SEQ,
        requested_since_seq,
    )

    live_heartbeat_seq = (
        current_server_last_seq + 1
    )

    live_heartbeat = (
        create_heartbeat(
            seq=live_heartbeat_seq
        )
    )

    await websocket.send(
        json.dumps(
            live_heartbeat
        )
    )

    print(
        f"Sent live seq {live_heartbeat_seq}: "
        "fund.heartbeat"
    )

    print(
        "Recovery session is now holding "
        "the socket open briefly."
    )

    await asyncio.sleep(
        POST_RECOVERY_HOLD_SECONDS
    )

    print()
    print(
        "Recovery session completed normally."
    )


class DisconnectScenario:
    """Track which fake Eagle connection phase is active."""

    def __init__(
        self,
    ) -> None:
        """Create a new two-session scenario."""

        self._connection_count = 0

    @property
    def connection_count(
        self,
    ) -> int:
        """Return number of accepted BTS connections."""

        return self._connection_count

    async def handle_client(
        self,
        websocket: ServerConnection,
    ) -> None:
        """Run first-session disconnect or recovery session."""

        self._connection_count += 1

        connection_number = (
            self._connection_count
        )

        print()
        print("=" * 60)
        print(
            f"BTS CONNECTION #{connection_number}"
        )
        print("=" * 60)

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

            if connection_number == 1:
                await run_first_session(
                    websocket,
                    requested_since_seq=(
                        requested_since_seq
                    ),
                )

                return

            await run_recovery_session(
                websocket,
                requested_since_seq=(
                    requested_since_seq
                ),
            )

        except ConnectionClosed as error:
            print()
            print(
                "BTS connection closed:"
            )
            print(
                f"{type(error).__name__}: {error}"
            )

        except ValueError as error:
            print()
            print(
                f"Invalid BTS reconnect request: "
                f"{error}"
            )


async def main() -> None:
    """Run the disconnect/reconnect fake Eagle server."""

    scenario = DisconnectScenario()

    print("=" * 60)
    print(
        "Fake Eagle Disconnect / Reconnect Server"
    )
    print("=" * 60)
    print(
        f"Listening at ws://{HOST}:{PORT}"
    )
    print(
        "Mode: REAL WEBSOCKET DISCONNECT / REPLAY RECOVERY"
    )
    print(
        "Connection #1 will be deliberately interrupted."
    )
    print(
        "Connection #2 will replay from BTS's "
        "new durable since_seq."
    )
    print(
        "Press Ctrl+C to stop the server."
    )
    print("=" * 60)
    print()

    async with serve(
        scenario.handle_client,
        HOST,
        PORT,
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
            "Fake Eagle disconnect server stopped."
        )