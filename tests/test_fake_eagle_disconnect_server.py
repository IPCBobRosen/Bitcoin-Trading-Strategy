"""Tests for the fake Eagle disconnect/reconnect server."""

from scripts.fake_eagle_disconnect_server import (
    FIRST_SESSION_DISCONNECT_AFTER_SEQ,
    HISTORY_LAST_SEQ,
    INTENTIONAL_DISCONNECT_CODE,
    DisconnectScenario,
    create_heartbeat,
    create_hello,
    create_lifecycle_event,
    create_test_messages,
    is_lifecycle_message,
    lifecycle_replay_count,
    message_seq,
    messages_after_seq,
)


def test_history_last_seq_matches_test_history() -> None:
    """Historical last sequence should match generated history."""

    messages = create_test_messages()

    assert message_seq(
        messages[-1]
    ) == HISTORY_LAST_SEQ


def test_test_history_has_expected_sequences() -> None:
    """Historical stream should contain sequences one through five."""

    messages = create_test_messages()

    sequences = [
        message_seq(message)
        for message in messages
    ]

    assert sequences == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_test_history_contains_expected_message_types() -> None:
    """Historical stream should mix lifecycle and heartbeat frames."""

    messages = create_test_messages()

    message_types = [
        message["type"]
        for message in messages
    ]

    assert message_types == [
        "fund.entry",
        "fund.heartbeat",
        "fund.entry",
        "fund.heartbeat",
        "fund.exit",
    ]


def test_create_hello_preserves_reconnect_values() -> None:
    """Hello should report the supplied reconnect state."""

    hello = create_hello(
        since_seq=3,
        last_seq=5,
        replay_count=1,
    )

    assert hello["type"] == "fund.hello"
    assert hello["since_seq"] == 3
    assert hello["last_seq"] == 5
    assert hello["replay_count"] == 1
    assert hello["open_count"] == 0
    assert hello["open"] == []
    assert hello["env"] == "staging"


def test_create_heartbeat_preserves_sequence() -> None:
    """Heartbeat helper should create the expected control frame."""

    heartbeat = create_heartbeat(
        seq=6
    )

    assert heartbeat["type"] == "fund.heartbeat"
    assert heartbeat["seq"] == 6
    assert heartbeat["open_count"] == 0


def test_create_lifecycle_event_preserves_fields() -> None:
    """Lifecycle helper should preserve execution-neutral event data."""

    event = create_lifecycle_event(
        message_type="fund.entry",
        seq=10,
        event_id="event-010",
        signal_id="signal-010",
        intent="BUY_TO_OPEN",
    )

    assert event["type"] == "fund.entry"
    assert event["seq"] == 10
    assert event["event_id"] == "event-010"
    assert event["signal_id"] == "signal-010"
    assert event["env"] == "staging"

    assert (
        event["payload"]["intent"]
        == "BUY_TO_OPEN"
    )


def test_is_lifecycle_message_accepts_entry() -> None:
    """fund.entry should count as lifecycle replay."""

    event = create_lifecycle_event(
        message_type="fund.entry",
        seq=1,
        event_id="event-001",
        signal_id="signal-001",
        intent="BUY_TO_OPEN",
    )

    assert (
        is_lifecycle_message(event)
        is True
    )


def test_is_lifecycle_message_accepts_exit() -> None:
    """fund.exit should count as lifecycle replay."""

    event = create_lifecycle_event(
        message_type="fund.exit",
        seq=5,
        event_id="event-005",
        signal_id="signal-001",
        intent="SELL_TO_CLOSE",
    )

    assert (
        is_lifecycle_message(event)
        is True
    )


def test_is_lifecycle_message_rejects_heartbeat() -> None:
    """Heartbeat must not count toward lifecycle replay."""

    heartbeat = create_heartbeat(
        seq=2
    )

    assert (
        is_lifecycle_message(heartbeat)
        is False
    )


def test_messages_after_zero_returns_complete_history() -> None:
    """Fresh BTS state should receive all historical messages."""

    messages = messages_after_seq(
        since_seq=0
    )

    assert [
        message_seq(message)
        for message in messages
    ] == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_messages_after_three_returns_remaining_history() -> None:
    """Reconnect from sequence three should receive four and five."""

    messages = messages_after_seq(
        since_seq=3
    )

    assert [
        message_seq(message)
        for message in messages
    ] == [
        4,
        5,
    ]


def test_messages_after_history_returns_empty() -> None:
    """Fully current BTS state should require no historical replay."""

    messages = messages_after_seq(
        since_seq=HISTORY_LAST_SEQ
    )

    assert messages == []


def test_lifecycle_replay_count_ignores_heartbeats() -> None:
    """Replay count must count lifecycle frames only."""

    messages = messages_after_seq(
        since_seq=0
    )

    assert (
        lifecycle_replay_count(
            messages
        )
        == 3
    )


def test_lifecycle_replay_count_after_three_is_one() -> None:
    """Reconnect from three has only one remaining lifecycle event."""

    messages = messages_after_seq(
        since_seq=3
    )

    assert (
        lifecycle_replay_count(
            messages
        )
        == 1
    )


def test_disconnect_occurs_after_sequence_three() -> None:
    """First fake session should deliberately stop after seq three."""

    assert (
        FIRST_SESSION_DISCONNECT_AFTER_SEQ
        == 3
    )


def test_disconnect_uses_service_restart_code() -> None:
    """Intentional disconnect should use WebSocket service-restart code."""

    assert (
        INTENTIONAL_DISCONNECT_CODE
        == 1012
    )


def test_disconnect_scenario_starts_with_no_connections() -> None:
    """New scenario should not report phantom connections."""

    scenario = DisconnectScenario()

    assert scenario.connection_count == 0