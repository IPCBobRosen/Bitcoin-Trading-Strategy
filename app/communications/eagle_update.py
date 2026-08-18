"""Parse Eagle Fund update messages.

Version 1 of BTS recognizes fund.update messages so they can pass
safely through the Eagle communications layer.

These messages do NOT create TradeRequests and do NOT cause broker
orders. Stop-loss and trailing-stop management will be implemented
separately in a later version.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.communications.protocol import Environment


@dataclass(frozen=True, slots=True)
class EagleUpdate:
    """One validated Eagle fund.update message."""

    seq: int
    event_id: str
    signal_id: str
    timestamp: datetime
    environment: Environment
    update_type: str
    trail_stop: Decimal | None

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
    ) -> "EagleUpdate":
        """Validate and parse one raw fund.update payload."""

        if not isinstance(
            payload,
            dict,
        ):
            raise TypeError(
                "'payload' must be a dictionary."
            )

        message_type = payload.get(
            "type"
        )

        if message_type != "fund.update":
            raise ValueError(
                "EagleUpdate requires "
                "type='fund.update'."
            )

        seq = payload.get(
            "seq"
        )

        if (
            isinstance(seq, bool)
            or not isinstance(seq, int)
            or seq < 0
        ):
            raise ValueError(
                "fund.update 'seq' must be a "
                "non-negative integer."
            )

        event_id = payload.get(
            "event_id"
        )

        if (
            not isinstance(event_id, str)
            or not event_id.strip()
        ):
            raise ValueError(
                "fund.update 'event_id' must be "
                "a non-empty string."
            )

        signal_id = payload.get(
            "signal_id"
        )

        if (
            not isinstance(signal_id, str)
            or not signal_id.strip()
        ):
            raise ValueError(
                "fund.update 'signal_id' must be "
                "a non-empty string."
            )

        raw_timestamp = payload.get(
            "ts"
        )

        if (
            not isinstance(raw_timestamp, str)
            or not raw_timestamp.strip()
        ):
            raise ValueError(
                "fund.update 'ts' must be a "
                "non-empty string."
            )

        timestamp_text = (
            raw_timestamp.strip()
        )

        if timestamp_text.endswith(
            "Z"
        ):
            timestamp_text = (
                timestamp_text[:-1]
                + "+00:00"
            )

        try:
            timestamp = (
                datetime.fromisoformat(
                    timestamp_text
                )
            )

        except ValueError as error:
            raise ValueError(
                "fund.update 'ts' must be a "
                "valid ISO-8601 timestamp."
            ) from error

        raw_environment = payload.get(
            "env"
        )

        if (
            not isinstance(
                raw_environment,
                str,
            )
            or not raw_environment.strip()
        ):
            raise ValueError(
                "fund.update 'env' must be a "
                "non-empty string."
            )

        try:
            environment = Environment(
                raw_environment
                .strip()
                .lower()
            )

        except ValueError as error:
            raise ValueError(
                "fund.update contains an "
                "unsupported environment."
            ) from error

        update_type = payload.get(
            "update"
        )

        if (
            not isinstance(update_type, str)
            or not update_type.strip()
        ):
            raise ValueError(
                "fund.update 'update' must be "
                "a non-empty string."
            )

        raw_trail_stop = payload.get(
            "trail_stop"
        )

        if raw_trail_stop is None:
            raw_updates = payload.get(
                "updates"
            )

            if isinstance(
                raw_updates,
                dict,
            ):
                raw_trail_stop = (
                    raw_updates.get(
                        "trail_stop"
                    )
                )

        trail_stop: Decimal | None

        if raw_trail_stop is None:
            trail_stop = None

        else:
            if isinstance(
                raw_trail_stop,
                bool,
            ):
                raise ValueError(
                    "fund.update 'trail_stop' "
                    "must be numeric when present."
                )

            try:
                trail_stop = Decimal(
                    str(
                        raw_trail_stop
                    )
                )

            except Exception as error:
                raise ValueError(
                    "fund.update 'trail_stop' "
                    "must be numeric when present."
                ) from error

            if not trail_stop.is_finite():
                raise ValueError(
                    "fund.update 'trail_stop' "
                    "must be finite."
                )

        return cls(
            seq=seq,
            event_id=event_id.strip(),
            signal_id=signal_id.strip(),
            timestamp=timestamp,
            environment=environment,
            update_type=(
                update_type.strip()
            ),
            trail_stop=trail_stop,
        )