"""Object representing the Eagle fund.hello control frame."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.communications.protocol import Environment


@dataclass(frozen=True, slots=True)
class EagleHello:
    """Validated Eagle fund.hello connection snapshot.

    A fund.hello message is a control/snapshot frame.

    It is not an executable trading instruction.
    """

    message_type: str
    contract: str
    version: str
    capabilities: tuple[str, ...]
    flags: dict[str, Any]
    last_seq: int
    since_seq: int
    open_count: int
    open_positions: tuple[dict[str, Any], ...]
    open_by_channel: dict[str, Any] | None
    replay_count: int
    timestamp: datetime
    environment: Environment

    @classmethod
    def from_dict(
        cls,
        message: Mapping[str, Any],
    ) -> "EagleHello":
        """Validate a decoded fund.hello message."""

        required_fields = {
            "type",
            "contract",
            "version",
            "capabilities",
            "flags",
            "last_seq",
            "since_seq",
            "open_count",
            "open",
            "replay_count",
            "ts",
            "env",
        }

        missing_fields = required_fields.difference(message)

        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(
                f"Missing required fund.hello field(s): {missing}"
            )

        message_type = message["type"]
        contract = message["contract"]
        version = message["version"]
        capabilities = message["capabilities"]
        flags = message["flags"]
        last_seq = message["last_seq"]
        since_seq = message["since_seq"]
        open_count = message["open_count"]
        open_positions = message["open"]
        replay_count = message["replay_count"]
        timestamp_text = message["ts"]
        environment_text = message["env"]

        open_by_channel = message.get("open_by_channel")

        if message_type != "fund.hello":
            raise ValueError(
                "'type' must be 'fund.hello'."
            )

        if not isinstance(contract, str) or not contract.strip():
            raise ValueError(
                "'contract' must be a non-empty string."
            )

        if not isinstance(version, str) or not version.strip():
            raise ValueError(
                "'version' must be a non-empty string."
            )

        if not isinstance(capabilities, list):
            raise ValueError(
                "'capabilities' must be a JSON array."
            )

        if not all(
            isinstance(capability, str)
            for capability in capabilities
        ):
            raise ValueError(
                "'capabilities' must contain only strings."
            )

        if not isinstance(flags, dict):
            raise ValueError(
                "'flags' must be a JSON object."
            )

        cls._validate_non_negative_integer(
            last_seq,
            "last_seq",
        )

        cls._validate_non_negative_integer(
            since_seq,
            "since_seq",
        )

        cls._validate_non_negative_integer(
            open_count,
            "open_count",
        )

        cls._validate_non_negative_integer(
            replay_count,
            "replay_count",
        )

        if not isinstance(open_positions, list):
            raise ValueError(
                "'open' must be a JSON array."
            )

        if not all(
            isinstance(position, dict)
            for position in open_positions
        ):
            raise ValueError(
                "'open' must contain only JSON objects."
            )

        if open_count != len(open_positions):
            raise ValueError(
                "'open_count' must match the number of entries in 'open'."
            )

        if (
            open_by_channel is not None
            and not isinstance(open_by_channel, dict)
        ):
            raise ValueError(
                "'open_by_channel' must be a JSON object when supplied."
            )

        if not isinstance(timestamp_text, str):
            raise ValueError(
                "'ts' must be an ISO-8601 timestamp string."
            )

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

        return cls(
            message_type=message_type,
            contract=contract.strip(),
            version=version.strip(),
            capabilities=tuple(capabilities),
            flags=dict(flags),
            last_seq=last_seq,
            since_seq=since_seq,
            open_count=open_count,
            open_positions=tuple(
                dict(position)
                for position in open_positions
            ),
            open_by_channel=(
                dict(open_by_channel)
                if open_by_channel is not None
                else None
            ),
            replay_count=replay_count,
            timestamp=timestamp,
            environment=environment,
        )

    @staticmethod
    def _validate_non_negative_integer(
        value: Any,
        field_name: str,
    ) -> None:
        """Validate a non-negative integer hello field."""

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(
                f"'{field_name}' must be a non-negative integer."
            )