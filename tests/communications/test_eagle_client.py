import json

import pytest

from app.communications.eagle_client import EagleClient
from app.communications.protocol import Environment


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
    client = EagleClient()

    event = client._parse_message(make_valid_message())

    assert event.message_type == "fund.entry"
    assert event.seq == 1204
    assert event.environment == Environment.STAGING
    assert event.payload["intent"] == "BUY_TO_OPEN"


def test_parse_rejects_invalid_json() -> None:
    client = EagleClient()

    with pytest.raises(ValueError, match="not valid JSON"):
        client._parse_message('{"message_type":')


def test_parse_rejects_json_list() -> None:
    client = EagleClient()

    with pytest.raises(ValueError, match="JSON object"):
        client._parse_message('["BUY_TO_OPEN", "MBT"]')


def test_parse_rejects_non_string_input() -> None:
    client = EagleClient()

    with pytest.raises(TypeError, match="must be a string"):
        client._parse_message({"message_type": "fund.entry"})  # type: ignore[arg-type]