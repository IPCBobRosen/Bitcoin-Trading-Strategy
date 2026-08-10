"""Provide normalized broker-position snapshots to BTS."""

from collections.abc import Callable, Sequence
from typing import Protocol

from app.broker_position import BrokerPosition
from app.broker_position_adapter import (
    BrokerPositionAdapter,
    RawBrokerPosition,
)


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


class AdapterBrokerPositionProvider:
    """Retrieve raw broker positions and normalize them for BTS."""

    def __init__(
        self,
        raw_position_source: Callable[
            [],
            list[RawBrokerPosition]
            | tuple[RawBrokerPosition, ...],
        ],
        adapter: BrokerPositionAdapter | None = None,
    ) -> None:
        """Create an adapter-backed broker-position provider.

        Args:
            raw_position_source:
                Callable that returns the broker's current raw
                position snapshot.

            adapter:
                Optional BrokerPositionAdapter.

                When omitted, a default adapter is created.
        """

        if not callable(
            raw_position_source
        ):
            raise TypeError(
                "'raw_position_source' must be callable."
            )

        if (
            adapter is not None
            and not isinstance(
                adapter,
                BrokerPositionAdapter,
            )
        ):
            raise TypeError(
                "'adapter' must be a BrokerPositionAdapter "
                "when supplied."
            )

        self._raw_position_source = (
            raw_position_source
        )

        self._adapter = (
            adapter
            if adapter is not None
            else BrokerPositionAdapter()
        )

    def get_positions(self) -> tuple[BrokerPosition, ...]:
        """Retrieve and normalize the current broker positions."""

        raw_positions = (
            self._raw_position_source()
        )

        return self._adapter.adapt(
            raw_positions
        )