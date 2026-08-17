"""Tests for the Eagle connection-only safety harness."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.communications.eagle_client import EagleClient
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.replay_tracker import ReplayTracker

from scripts.test_eagle_connection_only import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    DEFAULT_MAX_MESSAGES,
    ConnectionOnlyResult,
    build_client,
)


def create_success_result() -> ConnectionOnlyResult:
    """Create one representative successful session result."""

    return ConnectionOnlyResult(
        hello_count=1,
        heartbeat_count=2,
        lifecycle_count=3,
        accepted_lifecycle_count=2,
        duplicate_lifecycle_count=1,
        out_of_sequence_count=0,
        replay_expected=2,
        replay_processed=2,
        replay_complete=True,
        last_durable_seq=102,
    )


def create_hello(
    *,
    last_seq: int = 102,
    since_seq: int = 100,
    replay_count: int = 2,
) -> EagleHello:
    """Create a valid Eagle hello frame for replay tests."""

    return EagleHello.from_dict(
        {
            "type": "fund.hello",
            "contract": "1.2.0",
            "version": "1.2.0",
            "capabilities": [],
            "flags": {},
            "last_seq": last_seq,
            "since_seq": since_seq,
            "open_count": 0,
            "open": [],
            "replay_count": replay_count,
            "ts": "2026-08-17T12:00:00+00:00",
            "env": "staging",
        }
    )


def create_lifecycle_event(
    *,
    event_id: str,
    signal_id: str,
    seq: int,
) -> IncomingLifecycleEvent:
    """Create a valid Eagle lifecycle event for replay tests."""

    return IncomingLifecycleEvent.from_dict(
        {
            "type": "fund.entry",
            "seq": seq,
            "event_id": event_id,
            "signal_id": signal_id,
            "ts": "2026-08-17T12:00:01+00:00",
            "env": "staging",
            "payload": {
                "intent": "BUY_TO_OPEN",
            },
        }
    )


def process_replay_event(
    *,
    processor: EventProcessor,
    replay_tracker: ReplayTracker,
    event: IncomingLifecycleEvent,
) -> EventProcessStatus:
    """Apply the same accepted-only replay rule as the harness."""

    replay_was_complete = (
        replay_tracker.replay_complete
    )

    process_result = processor.process(
        event
    )

    if (
        not replay_was_complete
        and process_result.status
        is EventProcessStatus.ACCEPTED
    ):
        replay_tracker.record_lifecycle_event(
            event
        )

    return process_result.status


def test_default_heartbeat_timeout() -> None:
    """Harness should use the established Eagle heartbeat timeout."""

    assert DEFAULT_HEARTBEAT_TIMEOUT_SECONDS == 45


def test_default_max_messages_is_positive() -> None:
    """Harness should stop after a bounded number of messages."""

    assert DEFAULT_MAX_MESSAGES > 0


def test_result_is_immutable() -> None:
    """Connection-only results must remain immutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.hello_count = 99  # type: ignore[misc]


def test_build_client_without_cursor(
    tmp_path,
) -> None:
    """Empty EventStore should create client without since_seq."""

    database_path = (
        tmp_path
        / "events.db"
    )

    store = EventStore(
        database_path
    )

    client = build_client(
        uri="ws://localhost:8765",
        api_key=None,
        event_store=store,
    )

    assert isinstance(
        client,
        EagleClient,
    )

    assert client.since_seq is None

    assert (
        client._connection_uri()
        == "ws://localhost:8765"
    )


def test_build_client_uses_durable_cursor(
    tmp_path,
) -> None:
    """Reconnect client must use last durable Eagle sequence."""

    database_path = (
        tmp_path
        / "events.db"
    )

    store = EventStore(
        database_path
    )

    store.mark_seq_processed(
        250
    )

    client = build_client(
        uri="ws://localhost:8765",
        api_key=None,
        event_store=store,
    )

    assert client.since_seq == 250

    assert (
        client._connection_uri()
        == "ws://localhost:8765?since_seq=250"
    )


def test_build_client_preserves_existing_query(
    tmp_path,
) -> None:
    """since_seq should append without destroying query parameters."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    store.mark_seq_processed(
        250
    )

    client = build_client(
        uri=(
            "wss://example.com/ipc/v1/stream"
            "?channels=all"
        ),
        api_key=None,
        event_store=store,
    )

    assert (
        client._connection_uri()
        == (
            "wss://example.com/ipc/v1/stream"
            "?channels=all&since_seq=250"
        )
    )


def test_build_client_preserves_api_key(
    tmp_path,
) -> None:
    """Connection-only harness should support Eagle authentication."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    client = build_client(
        uri="wss://example.com/ipc/v1/stream",
        api_key="test-key",
        event_store=store,
    )

    assert client.has_api_key is True

    assert (
        client._connection_headers()
        == {
            "x-api-key": "test-key",
        }
    )


def test_connection_only_script_contains_no_ib_imports() -> None:
    """Harness must contain no Interactive Brokers execution path."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    forbidden_tokens = (
        "ibapi",
        "IBExecutionClient",
        "IBOrderFactory",
        "IBBrokerClient",
        "placeOrder",
        "place_order_function",
    )

    for token in forbidden_tokens:
        assert token not in source


def test_connection_only_script_contains_no_broker_client() -> None:
    """Harness must not instantiate broker clients."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "FakeBrokerClient" not in source
    assert "BrokerPositionProvider" not in source


def test_connection_only_script_imports_no_trade_coordinator() -> None:
    """Harness must not import the trading-decision layer."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.trade_coordinator import"
        not in source
    )

    assert (
        "import app.trade_coordinator"
        not in source
    )


def test_connection_only_script_imports_no_trade_request() -> None:
    """Harness must not import executable TradeRequest code."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.communications.trade_request import"
        not in source
    )

    assert (
        "import app.communications.trade_request"
        not in source
    )


def test_connection_only_script_contains_no_resume_manager() -> None:
    """Harness must not resume BTS trading."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "ResumeManager" not in source


def test_connection_only_script_uses_environment_api_key() -> None:
    """API key should be available through environment configuration."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "BTS_EAGLE_API_KEY" in source


def test_connection_only_script_does_not_embed_real_api_key() -> None:
    """Source must not contain an obvious embedded secret."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_eagle_connection_only.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert "SUPER-SECRET" not in source
    assert "Bearer " not in source


def test_result_fields_preserve_counts() -> None:
    """Summary should preserve all observed message counts."""

    result = create_success_result()

    assert result.hello_count == 1
    assert result.heartbeat_count == 2
    assert result.lifecycle_count == 3
    assert result.accepted_lifecycle_count == 2
    assert result.duplicate_lifecycle_count == 1
    assert result.out_of_sequence_count == 0
    assert result.replay_expected == 2
    assert result.replay_processed == 2
    assert result.replay_complete is True
    assert result.last_durable_seq == 102


def test_accepted_lifecycle_event_advances_replay(
    tmp_path,
) -> None:
    """An accepted lifecycle event must advance replay progress."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    store.mark_seq_processed(
        100
    )

    processor = EventProcessor(
        store
    )

    replay_tracker = ReplayTracker()

    replay_tracker.process_hello(
        create_hello(
            last_seq=101,
            since_seq=100,
            replay_count=1,
        )
    )

    event = create_lifecycle_event(
        event_id="accepted-event-101",
        signal_id="accepted-signal-101",
        seq=101,
    )

    status = process_replay_event(
        processor=processor,
        replay_tracker=replay_tracker,
        event=event,
    )

    assert status is EventProcessStatus.ACCEPTED
    assert replay_tracker.processed_replay_count == 1
    assert replay_tracker.replay_complete is True
    assert store.get_last_seq() == 101


def test_duplicate_lifecycle_event_does_not_advance_replay(
    tmp_path,
) -> None:
    """A duplicate event must not satisfy Eagle replay progress."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    original_event = create_lifecycle_event(
        event_id="duplicate-event-101",
        signal_id="duplicate-signal-101",
        seq=101,
    )

    initial_processor = EventProcessor(
        store
    )

    initial_result = initial_processor.process(
        original_event
    )

    assert (
        initial_result.status
        is EventProcessStatus.ACCEPTED
    )

    processor = EventProcessor(
        store
    )

    replay_tracker = ReplayTracker()

    replay_tracker.process_hello(
        create_hello(
            last_seq=102,
            since_seq=101,
            replay_count=1,
        )
    )

    status = process_replay_event(
        processor=processor,
        replay_tracker=replay_tracker,
        event=original_event,
    )

    assert status is EventProcessStatus.DUPLICATE_EVENT
    assert replay_tracker.processed_replay_count == 0
    assert replay_tracker.replay_complete is False
    assert store.get_last_seq() == 101


def test_out_of_sequence_lifecycle_event_does_not_advance_replay(
    tmp_path,
) -> None:
    """An old sequence must not satisfy Eagle replay progress."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    store.mark_seq_processed(
        100
    )

    processor = EventProcessor(
        store
    )

    replay_tracker = ReplayTracker()

    replay_tracker.process_hello(
        create_hello(
            last_seq=101,
            since_seq=100,
            replay_count=1,
        )
    )

    old_event = create_lifecycle_event(
        event_id="old-event-099",
        signal_id="old-signal-099",
        seq=99,
    )

    status = process_replay_event(
        processor=processor,
        replay_tracker=replay_tracker,
        event=old_event,
    )

    assert status is EventProcessStatus.OUT_OF_SEQUENCE
    assert replay_tracker.processed_replay_count == 0
    assert replay_tracker.replay_complete is False
    assert store.get_last_seq() == 100


def test_rejected_events_cannot_falsely_complete_replay(
    tmp_path,
) -> None:
    """Rejected events must not falsely drain an announced replay."""

    store = EventStore(
        tmp_path
        / "events.db"
    )

    accepted_before_reconnect = (
        create_lifecycle_event(
            event_id="already-processed-100",
            signal_id="signal-100",
            seq=100,
        )
    )

    processor = EventProcessor(
        store
    )

    initial_result = processor.process(
        accepted_before_reconnect
    )

    assert (
        initial_result.status
        is EventProcessStatus.ACCEPTED
    )

    replay_tracker = ReplayTracker()

    replay_tracker.process_hello(
        create_hello(
            last_seq=102,
            since_seq=100,
            replay_count=2,
        )
    )

    duplicate_status = process_replay_event(
        processor=processor,
        replay_tracker=replay_tracker,
        event=accepted_before_reconnect,
    )

    old_event = create_lifecycle_event(
        event_id="new-id-old-seq-099",
        signal_id="signal-099",
        seq=99,
    )

    old_status = process_replay_event(
        processor=processor,
        replay_tracker=replay_tracker,
        event=old_event,
    )

    assert (
        duplicate_status
        is EventProcessStatus.DUPLICATE_EVENT
    )

    assert (
        old_status
        is EventProcessStatus.OUT_OF_SEQUENCE
    )

    assert replay_tracker.processed_replay_count == 0
    assert replay_tracker.replay_complete is False
    assert store.get_last_seq() == 100