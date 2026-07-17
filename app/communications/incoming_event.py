"""Objects representing lifecycle events received from Eagle."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.communications.protocol import Environment


@dataclass(frozen=True, slots=True)
class IncomingLifecycleEvent:
    """A validated lifecycle event received from the IPC Signal API.

    This object preserves the communication envelope and the remaining
    event-specific payload. It does not represent an executable order.
    """

    message_type: str
    seq: int
    event_id: str
    signal_id: str
    timestamp: datetime
    environment: Environment
    payload: dict[str, Any]

    @classmethod
    def from_dict(
        cls,
        message: Mapping[str, Any],
    ) -> "IncomingLifecycleEvent":
        """Validate a decoded JSON message and create an event object."""

        required_fields = {
            "type",
            "seq",
            "event_id",
            "signal_id",
            "ts",
            "env",
        }

        missing_fields = required_fields.difference(message)

        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"Missing required field(s): {missing}")

        message_type = message["type"]
        seq = message["seq"]
        event_id = message["event_id"]
        signal_id = message["signal_id"]
        timestamp_text = message["ts"]
        environment_text = message["env"]

        if not isinstance(message_type, str) or not message_type.strip():
            raise ValueError("'type' must be a non-empty string.")

        if (
            not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq < 0
        ):
            raise ValueError("'seq' must be a non-negative integer.")

        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("'event_id' must be a non-empty string.")

        if not isinstance(signal_id, str) or not signal_id.strip():
            raise ValueError("'signal_id' must be a non-empty string.")

        if not isinstance(timestamp_text, str):
            raise ValueError("'ts' must be an ISO-8601 timestamp string.")

        try:
            timestamp = datetime.fromisoformat(
                timestamp_text.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError(
                "'ts' must be a valid ISO-8601 timestamp."
            ) from error

        try:
            environment = Environment(environment_text)
        except ValueError as error:
            raise ValueError(
                "'env' must be either 'staging' or 'live'."
            ) from error

        envelope_fields = required_fields

        payload = {
            key: value
            for key, value in message.items()
            if key not in envelope_fields
        }

        return cls(
            message_type=message_type,
            seq=seq,
            event_id=event_id,
            signal_id=signal_id,
            timestamp=timestamp,
            environment=environment,
            payload=payload,
        )