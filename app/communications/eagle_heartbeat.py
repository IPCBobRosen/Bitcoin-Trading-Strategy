"""Object representing the Eagle fund.heartbeat control frame."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class EagleHeartbeat:
    """Validated Eagle fund.heartbeat liveness frame.

    A heartbeat is an informational/control message.

    It is not an executable trading instruction.

    The heartbeat sequence number advances Eagle's global sequence cursor
    and must eventually be persisted by BTS.
    """

    message_type: str
    seq: int
    open_count: int
    open_count_by_channel: dict[str, Any]

    @classmethod
    def from_dict(
        cls,
        message: Mapping[str, Any],
    ) -> "EagleHeartbeat":
        """Validate a decoded fund.heartbeat message."""

        required_fields = {
            "type",
            "seq",
            "open_count",
            "open_count_by_channel",
        }

        missing_fields = required_fields.difference(message)

        if missing_fields:
            missing = ", ".join(sorted(missing_fields))

            raise ValueError(
                f"Missing required fund.heartbeat field(s): {missing}"
            )

        message_type = message["type"]
        seq = message["seq"]
        open_count = message["open_count"]
        open_count_by_channel = message["open_count_by_channel"]

        if message_type != "fund.heartbeat":
            raise ValueError(
                "'type' must be 'fund.heartbeat'."
            )

        cls._validate_non_negative_integer(
            seq,
            "seq",
        )

        cls._validate_non_negative_integer(
            open_count,
            "open_count",
        )

        if not isinstance(open_count_by_channel, dict):
            raise ValueError(
                "'open_count_by_channel' must be a JSON object."
            )

        return cls(
            message_type=message_type,
            seq=seq,
            open_count=open_count,
            open_count_by_channel=dict(open_count_by_channel),
        )

    @staticmethod
    def _validate_non_negative_integer(
        value: Any,
        field_name: str,
    ) -> None:
        """Validate a non-negative integer heartbeat field."""

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(
                f"'{field_name}' must be a non-negative integer."
            )