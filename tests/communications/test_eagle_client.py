import json

import pytest

from app.communications.eagle_client import EagleClient
from app.communications.protocol import Environment
from unittest.mock import AsyncMock, patch


def make_valid_message() -> str:
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


def test_parse_valid_message() -> None:
    client = EagleClient("ws://localhost:8765")

    event = client._parse_message(make_valid_message())

    assert event.message_type == "fund.entry"
    assert event.seq == 1204
    assert event.environment == Environment.STAGING
    assert event.payload["intent"] == "BUY_TO_OPEN"


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
        client._parse_message({"message_type": "fund.entry"})  # type: ignore[arg-type]


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
        ):
            received_events = []

            async for event in client.listen():
                received_events.append(event)

        assert len(received_events) == 2
        assert received_events[0].seq == 1
        assert received_events[0].event_id == "event-001"
        assert received_events[1].seq == 2
        assert received_events[1].event_id == "event-002"

    import asyncio

    asyncio.run(run_test())