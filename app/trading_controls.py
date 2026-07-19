"""Runtime controls that may be changed by the trader."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class TradingSettingsSnapshot:
    """Immutable settings captured for one future trade request."""

    symbol: str
    quantity: int
    stop_loss_points: Decimal


class TradingControls:
    """Trader-adjustable runtime settings for future trade requests.

    The application starts paused for safety. Changes made through this class
    affect settings snapshots created after the change. Existing snapshots and
    existing trade requests are not modified.
    """

    def __init__(
        self,
        symbol: str = "MBT",
        quantity: int = 1,
        stop_loss_points: Decimal | int | float | str = Decimal("500"),
    ) -> None:
        self._paused = True
        self._symbol = self._validate_symbol(symbol)
        self._quantity = self._validate_quantity(quantity)
        self._stop_loss_points = self._validate_stop_loss(
            stop_loss_points
        )

    @property
    def is_paused(self) -> bool:
        """Return True when new trade requests should be blocked."""

        return self._paused

    @property
    def symbol(self) -> str:
        """Return the currently configured contract symbol."""

        return self._symbol

    @property
    def quantity(self) -> int:
        """Return the currently configured number of contracts."""

        return self._quantity

    @property
    def stop_loss_points(self) -> Decimal:
        """Return the current stop-loss distance in price points."""

        return self._stop_loss_points

    def pause(self) -> None:
        """Prevent future incoming signals from becoming trade requests."""

        self._paused = True

    def resume(self) -> None:
        """Allow future valid signals to become trade requests."""

        self._paused = False

    def update(
        self,
        *,
        symbol: str | None = None,
        quantity: int | None = None,
        stop_loss_points: Decimal | int | float | str | None = None,
    ) -> None:
        """Update one or more settings used by future trade requests.

        All supplied values are validated before any settings are changed.
        This prevents a partially completed update.
        """

        new_symbol = self._symbol
        new_quantity = self._quantity
        new_stop_loss_points = self._stop_loss_points

        if symbol is not None:
            new_symbol = self._validate_symbol(symbol)

        if quantity is not None:
            new_quantity = self._validate_quantity(quantity)

        if stop_loss_points is not None:
            new_stop_loss_points = self._validate_stop_loss(
                stop_loss_points
            )

        self._symbol = new_symbol
        self._quantity = new_quantity
        self._stop_loss_points = new_stop_loss_points

    def create_snapshot(self) -> TradingSettingsSnapshot:
        """Capture immutable settings for a future trade request."""

        return TradingSettingsSnapshot(
            symbol=self._symbol,
            quantity=self._quantity,
            stop_loss_points=self._stop_loss_points,
        )

    @staticmethod
    def _validate_symbol(symbol: Any) -> str:
        """Validate and normalize a contract symbol."""

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("'symbol' must be a non-empty string.")

        return symbol.strip().upper()

    @staticmethod
    def _validate_quantity(quantity: Any) -> int:
        """Validate the number of contracts."""

        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
        ):
            raise ValueError("'quantity' must be a positive integer.")

        return quantity

    @staticmethod
    def _validate_stop_loss(
        stop_loss_points: Any,
    ) -> Decimal:
        """Validate and normalize the stop-loss distance."""

        if isinstance(stop_loss_points, bool):
            raise ValueError(
                "'stop_loss_points' must be a positive number."
            )

        try:
            value = Decimal(str(stop_loss_points))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise ValueError(
                "'stop_loss_points' must be a positive number."
            ) from error

        if not value.is_finite() or value <= 0:
            raise ValueError(
                "'stop_loss_points' must be a positive number."
            )

        return value