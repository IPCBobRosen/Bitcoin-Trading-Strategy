"""Provide normalized broker-position snapshots to BTS."""

from collections.abc import Sequence
from typing import Protocol

from app.broker_position import BrokerPosition


class BrokerPositionProvider(Protocol):
    """Interface for retrieving the broker's current open positions."""

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        """Return the broker's current normalized open positions."""


class StaticBrokerPositionProvider:
    """Simple deterministic provider used for local integration testing."""

    def __init__(
        self,
        positions: Sequence[BrokerPosition] = (),
    ) -> None:
        """Create a provider with a fixed broker-position snapshot."""

        normalized_positions: list[BrokerPosition] = []

        for position in positions:
            if not isinstance(
                position,
                BrokerPosition,
            ):
                raise TypeError(
                    "'positions' must contain only BrokerPosition objects."
                )

            normalized_positions.append(
                position
            )

        self._positions = tuple(
            normalized_positions
        )

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        """Return the configured broker-position snapshot."""

        return self._positions