"""Broker-client interface for retrieving raw position snapshots."""

from typing import Protocol

from app.broker_position_adapter import RawBrokerPosition


class BrokerClient(Protocol):
    """Interface for retrieving raw broker positions."""

    def get_raw_positions(
        self,
    ) -> tuple[RawBrokerPosition, ...]:
        """Return the broker's current raw open-position snapshot."""


class FakeBrokerClient:
    """Deterministic broker client used for local tests."""

    def __init__(
        self,
        positions: tuple[RawBrokerPosition, ...] = (),
    ) -> None:
        """Create a fake broker client with a fixed position snapshot."""

        normalized_positions: list[RawBrokerPosition] = []

        for position in positions:
            if not isinstance(
                position,
                RawBrokerPosition,
            ):
                raise TypeError(
                    "'positions' must contain only RawBrokerPosition objects."
                )

            normalized_positions.append(
                position
            )

        self._positions = tuple(
            normalized_positions
        )

    def get_raw_positions(
        self,
    ) -> tuple[RawBrokerPosition, ...]:
        """Return the configured raw broker-position snapshot."""

        return self._positions