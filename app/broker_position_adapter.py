"""Normalize raw broker position data for BTS reconciliation."""

from dataclasses import dataclass
from typing import Any

from app.broker_position import BrokerPosition


@dataclass(frozen=True, slots=True)
class RawBrokerPosition:
    """Simple raw broker-position record used by the adapter."""

    symbol: str
    quantity: int
    signal_id: str | None = None


class BrokerPositionAdapter:
    """Convert raw broker positions into normalized BrokerPosition objects."""

    def adapt(
        self,
        raw_positions: list[RawBrokerPosition]
        | tuple[RawBrokerPosition, ...],
    ) -> tuple[BrokerPosition, ...]:
        """Normalize raw broker positions.

        Positive quantity becomes LONG.

        Negative quantity becomes SHORT.

        Zero quantity represents a flat position and is omitted.
        """

        if not isinstance(
            raw_positions,
            (list, tuple),
        ):
            raise TypeError(
                "'raw_positions' must be a list or tuple."
            )

        normalized_positions: list[BrokerPosition] = []

        for raw_position in raw_positions:
            if not isinstance(
                raw_position,
                RawBrokerPosition,
            ):
                raise TypeError(
                    "'raw_positions' must contain only "
                    "RawBrokerPosition objects."
                )

            if (
                not isinstance(raw_position.symbol, str)
                or not raw_position.symbol.strip()
            ):
                raise ValueError(
                    "Raw broker position 'symbol' must be "
                    "a non-empty string."
                )

            if (
                not isinstance(raw_position.quantity, int)
                or isinstance(raw_position.quantity, bool)
            ):
                raise ValueError(
                    "Raw broker position 'quantity' must be an integer."
                )

            if raw_position.quantity == 0:
                continue

            side = (
                "LONG"
                if raw_position.quantity > 0
                else "SHORT"
            )

            quantity = abs(
                raw_position.quantity
            )

            normalized_positions.append(
                BrokerPosition(
                    symbol=raw_position.symbol,
                    side=side,
                    quantity=quantity,
                    signal_id=raw_position.signal_id,
                )
            )

        return tuple(
            normalized_positions
        )