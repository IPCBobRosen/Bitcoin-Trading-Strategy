"""Tests for the Eagle WebSocket client."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from websockets import Headers, InvalidStatus, Response

from app.communications.eagle_client import (
    EagleAuthenticationError,
    EagleClient,
    EagleRateLimitError,
)
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import Environment
from app.communications.eagle_heartbeat import EagleHeartbeat


def make_valid_message() -> str:
    """Create one valid Eagle lifecycle-event JSON message."""

    message = {
        "type": "fund.entry",
        "seq": 1204,
        "event_id": "event-001",
        "signal_id": "signal-001",
        "ts": "2026-07-17T12:00:00+00:00",
        "env": "staging",
        "payload": {
            "intent": "BUY_TO_OPEN",
            "symbol": "MBT",
        },
    }

    return json.dumps(message)


def make_valid_hello_message() -> str:
    """Create one valid Eagle fund.hello JSON message."""

    message = {
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
        "last_seq": 1042,
        "since_seq": 0,
        "open_count": 0,
        "open": [],
        "replay_count": 0,
        "ts": "2026-07-13T17:04:37.456Z",
        "env": "staging",
    }

    return json.dumps(message)


def make_valid_heartbeat_message() -> str:
    """Create one valid Eagle fund.heartbeat JSON message."""

    message = {
        "type": "fund.heartbeat",
        "seq": 1043,
        "open_count": 1,
        "open_count_by_channel": {
            "fund": 1,
            "apollo": 0,
            "hermes": 0,
            "athena": 0,
            "moab": 0,
        },
    }

    return json.dumps(message)


def test_client_accepts_uri_without_api_key() -> None:
    """Local development should still work without authentication."""

    client = EagleClient("ws://localhost:8765")

    assert client.uri == "ws://localhost:8765"
    assert client.has_api_key is False


def test_client_accepts_api_key() -> None:
    """A production client should accept an Eagle API key."""

    client = EagleClient(
        "wss://example.com/ipc/v1/stream",
        api_key="test-api-key",
    )

    assert client.has_api_key is True


def test_connection_headers_include_api_key() -> None:
    """Configured API keys should be sent with the handshake."""

    client = EagleClient(
        "wss://example.com/ipc/v1/stream",
        api_key="test-api-key",
    )

    headers = client._connection_headers()

    assert headers == {
        "x-api-key": "test-api-key",
    }


def test_connection_headers_are_none_without_api_key() -> None:
    """Local unauthenticated connections should send no auth header."""

    client = EagleClient("ws://localhost:8765")

    assert client._connection_headers() is None


def test_empty_api_key_is_rejected() -> None:
    """A supplied API key must contain text."""

    with pytest.raises(
        ValueError,
        match="'api_key' must be a non-empty string",
    ):
        EagleClient(
            "wss://example.com/ipc/v1/stream",
            api_key="",
        )


def test_parse_valid_message() -> None:
    """Lifecycle JSON should become IncomingLifecycleEvent."""

    client = EagleClient("ws://localhost:8765")

    event = client._parse_message(make_valid_message())

    assert isinstance(event, IncomingLifecycleEvent)
    assert event.message_type == "fund.entry"
    assert event.seq == 1204
    assert event.environment == Environment.STAGING
    assert event.payload["intent"] == "BUY_TO_OPEN"


def test_parse_hello_message() -> None:
    """fund.hello JSON should become EagleHello."""

    client = EagleClient("ws://localhost:8765")

    message = client._parse_message(
        make_valid_hello_message()
    )

    assert isinstance(message, EagleHello)
    assert message.message_type == "fund.hello"
    assert message.last_seq == 1042
    assert message.since_seq == 0
    assert message.replay_count == 0
    assert message.environment == Environment.STAGING


def test_parse_heartbeat_message() -> None:
    """fund.heartbeat JSON should become EagleHeartbeat."""

    client = EagleClient("ws://localhost:8765")

    message = client._parse_message(
        make_valid_heartbeat_message()
    )

    assert isinstance(message, EagleHeartbeat)
    assert message.message_type == "fund.heartbeat"
    assert message.seq == 1043
    assert message.open_count == 1
    assert message.open_count_by_channel["fund"] == 1


def test_parse_rejects_invalid_json() -> None:
    client = EagleClient("ws://localhost:8765")

    with pytest.raises(ValueError, match="must contain valid JSON"):
        client._parse_message('{"message_type":')


def test_parse_rejects_json_list() -> None:
    client = EagleClient("ws://localhost:8765")

    with pytest.raises(ValueError, match="JSON object"):
        client._parse_message('["BUY_TO_OPEN", "MBT"]')


def test_parse_rejects_non_string_input() -> None:
    client = EagleClient("ws://localhost:8765")

    with pytest.raises(TypeError, match="must be a string"):
        client._parse_message(
            {"message_type": "fund.entry"}  # type: ignore[arg-type]
        )


def test_listen_receives_multiple_messages() -> None:
    async def run_test() -> None:
        client = EagleClient("ws://localhost:8765")

        valid_message_1 = """
        {
            "type": "fund.entry",
            "seq": 1,
            "event_id": "event-001",
            "signal_id": "signal-001",
            "ts": "2026-07-19T14:05:53.804413+00:00",
            "env": "staging",
            "payload": {
                "intent": "BUY_TO_OPEN"
            }
        }
        """

        valid_message_2 = """
        {
            "type": "fund.entry",
            "seq": 2,
            "event_id": "event-002",
            "signal_id": "signal-002",
            "ts": "2026-07-19T14:06:53.804413+00:00",
            "env": "staging",
            "payload": {
                "intent": "SELL_TO_OPEN"
            }
        }
        """

        mock_websocket = AsyncMock()
        mock_websocket.__aiter__.return_value = [
            valid_message_1,
            valid_message_2,
        ]

        mock_connection = AsyncMock()
        mock_connection.__aenter__.return_value = mock_websocket

        with patch(
            "app.communications.eagle_client.connect",
            return_value=mock_connection,
        ) as mocked_connect:
            received_events = []

            async for event in client.listen():
                received_events.append(event)

        assert len(received_events) == 2

        assert isinstance(
            received_events[0],
            IncomingLifecycleEvent,
        )

        assert isinstance(
            received_events[1],
            IncomingLifecycleEvent,
        )

        assert received_events[0].seq == 1
        assert received_events[0].event_id == "event-001"
        assert received_events[1].seq == 2
        assert received_events[1].event_id == "event-002"

        mocked_connect.assert_called_once_with(
            "ws://localhost:8765",
            additional_headers=None,
            open_timeout=10,
            close_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=1_048_576,
        )

    asyncio.run(run_test())


def test_listen_routes_hello_and_lifecycle_event() -> None:
    """The receive loop should route control and lifecycle frames."""

    async def run_test() -> None:
        client = EagleClient("ws://localhost:8765")

        hello_message = make_valid_hello_message()
        lifecycle_message = make_valid_message()

        mock_websocket = AsyncMock()
        mock_websocket.__aiter__.return_value = [
            hello_message,
            lifecycle_message,
        ]

        mock_connection = AsyncMock()
        mock_connection.__aenter__.return_value = mock_websocket

        with patch(
            "app.communications.eagle_client.connect",
            return_value=mock_connection,
        ):
            received_messages = []

            async for message in client.listen():
                received_messages.append(message)

        assert len(received_messages) == 2

        assert isinstance(
            received_messages[0],
            EagleHello,
        )

        assert isinstance(
            received_messages[1],
            IncomingLifecycleEvent,
        )

        assert received_messages[0].message_type == "fund.hello"
        assert received_messages[1].message_type == "fund.entry"

    asyncio.run(run_test())


def test_listen_sends_api_key_header() -> None:
    """Authenticated Eagle connections must include x-api-key."""

    async def run_test() -> None:
        client = EagleClient(
            "wss://example.com/ipc/v1/stream",
            api_key="test-api-key",
        )

        mock_websocket = AsyncMock()
        mock_websocket.__aiter__.return_value = []

        mock_connection = AsyncMock()
        mock_connection.__aenter__.return_value = mock_websocket

        with patch(
            "app.communications.eagle_client.connect",
            return_value=mock_connection,
        ) as mocked_connect:
            async for _ in client.listen():
                pass

        mocked_connect.assert_called_once_with(
            "wss://example.com/ipc/v1/stream",
            additional_headers={
                "x-api-key": "test-api-key",
            },
            open_timeout=10,
            close_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=1_048_576,
        )

    asyncio.run(run_test())


def test_parse_retry_after_accepts_integer_seconds() -> None:
    """Retry-After integer values should be parsed."""

    assert EagleClient._parse_retry_after("10") == 10


def test_parse_retry_after_rejects_invalid_value() -> None:
    """Invalid Retry-After values should return None."""

    assert EagleClient._parse_retry_after("not-a-number") is None
    assert EagleClient._parse_retry_after("-1") is None
    assert EagleClient._parse_retry_after(None) is None


def test_handshake_401_becomes_authentication_error() -> None:
    """HTTP 401 should become EagleAuthenticationError."""

    response = Response(
        401,
        "Unauthorized",
        Headers(),
    )

    error = InvalidStatus(response)

    with pytest.raises(
        EagleAuthenticationError,
        match="authentication failed",
    ):
        EagleClient._raise_for_handshake_error(error)


def test_handshake_429_becomes_rate_limit_error() -> None:
    """HTTP 429 should preserve Retry-After information."""

    headers = Headers()
    headers["Retry-After"] = "10"

    response = Response(
        429,
        "Too Many Requests",
        headers,
    )

    error = InvalidStatus(response)

    with pytest.raises(EagleRateLimitError) as exc_info:
        EagleClient._raise_for_handshake_error(error)

    assert exc_info.value.retry_after_seconds == 10


def test_handshake_other_status_becomes_connection_error() -> None:
    """Unexpected HTTP handshake failures should become ConnectionError."""

    response = Response(
        503,
        "Service Unavailable",
        Headers(),
    )

    error = InvalidStatus(response)

    with pytest.raises(
        ConnectionError,
        match="HTTP 503",
    ):
        EagleClient._raise_for_handshake_error(error)


def test_authentication_error_does_not_expose_api_key() -> None:
    """Authentication failures must never leak the configured API key."""

    secret_key = "SUPER-SECRET-KEY"

    response = Response(
        401,
        "Unauthorized",
        Headers(),
    )

    error = InvalidStatus(response)

    with pytest.raises(EagleAuthenticationError) as exc_info:
        EagleClient._raise_for_handshake_error(error)

    assert secret_key not in str(exc_info.value)


    def test_listen_routes_hello_heartbeat_and_lifecycle_event() -> None:
     """The receive loop should route all current Eagle message types."""

    async def run_test() -> None:
        client = EagleClient("ws://localhost:8765")

        mock_websocket = AsyncMock()
        mock_websocket.__aiter__.return_value = [
            make_valid_hello_message(),
            make_valid_heartbeat_message(),
            make_valid_message(),
        ]

        mock_connection = AsyncMock()
        mock_connection.__aenter__.return_value = mock_websocket

        with patch(
            "app.communications.eagle_client.connect",
            return_value=mock_connection,
        ):
            received_messages = []

            async for message in client.listen():
                received_messages.append(message)

        assert len(received_messages) == 3

        assert isinstance(
            received_messages[0],
            EagleHello,
        )

        assert isinstance(
            received_messages[1],
            EagleHeartbeat,
        )

        assert isinstance(
            received_messages[2],
            IncomingLifecycleEvent,
        )

        assert received_messages[0].message_type == "fund.hello"
        assert received_messages[1].message_type == "fund.heartbeat"
        assert received_messages[2].message_type == "fund.entry"

    asyncio.run(run_test())