"""Interactive Brokers position client for BTS reconciliation."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.broker_position_adapter import RawBrokerPosition


@dataclass(frozen=True, slots=True)
class IBPositionRecord:
    """Raw position information received from Interactive Brokers."""

    account: str
    symbol: str
    position: Decimal | int | float | str
    con_id: int | None = None
    local_symbol: str | None = None
    trading_class: str | None = None
    last_trade_date: str | None = None
    average_cost: float | None = None


class IBBrokerClient:
    """Collect an Interactive Brokers position snapshot.

    This first implementation models the IBKR reqPositions callback
    lifecycle without opening a real TWS or IB Gateway connection.

    A future transport layer will call:
        begin_position_snapshot()
        receive_position(...)
        finish_position_snapshot()
    """

    def __init__(self) -> None:
        """Create an IB broker client with no completed snapshot."""

        self._snapshot_in_progress = False
        self._snapshot_complete = False
        self._positions: list[RawBrokerPosition] = []

    @property
    def snapshot_in_progress(self) -> bool:
        """Return True while an IB position snapshot is being collected."""

        return self._snapshot_in_progress

    @property
    def snapshot_complete(self) -> bool:
        """Return True after the IB positionEnd equivalent is received."""

        return self._snapshot_complete

    def begin_position_snapshot(self) -> None:
        """Begin collecting a fresh IB position snapshot."""

        self._positions = []
        self._snapshot_in_progress = True
        self._snapshot_complete = False

    def receive_position(
        self,
        position: IBPositionRecord,
    ) -> None:
        """Process one IB position callback."""

        if not self._snapshot_in_progress:
            raise RuntimeError(
                "IB position snapshot has not been started."
            )

        if not isinstance(
            position,
            IBPositionRecord,
        ):
            raise TypeError(
                "'position' must be an IBPositionRecord."
            )

        raw_quantity = self._normalize_quantity(
            position.position
        )

        if raw_quantity == 0:
            return

        symbol = self._select_symbol(
            position
        )

        self._positions.append(
            RawBrokerPosition(
                symbol=symbol,
                quantity=raw_quantity,
                signal_id=None,
            )
        )

    def finish_position_snapshot(self) -> None:
        """Mark the initial IB position snapshot as complete."""

        if not self._snapshot_in_progress:
            raise RuntimeError(
                "IB position snapshot has not been started."
            )

        self._snapshot_in_progress = False
        self._snapshot_complete = True

    def get_raw_positions(
        self,
    ) -> tuple[RawBrokerPosition, ...]:
        """Return the completed raw broker-position snapshot."""

        if not self._snapshot_complete:
            raise RuntimeError(
                "IB position snapshot is not complete."
            )

        return tuple(
            self._positions
        )

    @staticmethod
    def _select_symbol(
        position: IBPositionRecord,
    ) -> str:
        """Choose the best available IB contract identifier."""

        if (
            position.local_symbol is not None
            and position.local_symbol.strip()
        ):
            return position.local_symbol.strip()

        if (
            isinstance(position.symbol, str)
            and position.symbol.strip()
        ):
            return position.symbol.strip()

        raise ValueError(
            "IB position must contain a symbol or local_symbol."
        )

    @staticmethod
    def _normalize_quantity(
        quantity: Any,
    ) -> int:
        """Convert an IB position quantity to an integer contract count."""

        if isinstance(
            quantity,
            bool,
        ):
            raise ValueError(
                "IB position quantity must be numeric."
            )

        try:
            normalized = Decimal(
                str(quantity)
            )

        except Exception as error:
            raise ValueError(
                "IB position quantity must be numeric."
            ) from error

        if not normalized.is_finite():
            raise ValueError(
                "IB position quantity must be finite."
            )

        integral = normalized.to_integral_value()

        if normalized != integral:
            raise ValueError(
                "IB futures position quantity must be a whole number."
            )

        return int(
            integral
        )