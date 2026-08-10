"""Normalized broker position representation for BTS reconciliation."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """Broker-neutral open-position snapshot used by BTS."""

    symbol: str
    side: str
    quantity: int
    signal_id: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize broker position fields."""

        normalized_symbol = self._validate_symbol(
            self.symbol
        )

        normalized_side = self._validate_side(
            self.side
        )

        normalized_quantity = self._validate_quantity(
            self.quantity
        )

        normalized_signal_id = self._validate_signal_id(
            self.signal_id
        )

        object.__setattr__(
            self,
            "symbol",
            normalized_symbol,
        )

        object.__setattr__(
            self,
            "side",
            normalized_side,
        )

        object.__setattr__(
            self,
            "quantity",
            normalized_quantity,
        )

        object.__setattr__(
            self,
            "signal_id",
            normalized_signal_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a reconciliation-friendly dictionary."""

        result: dict[str, Any] = {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
        }

        if self.signal_id is not None:
            result["signal_id"] = self.signal_id

        return result

    @staticmethod
    def _validate_symbol(symbol: Any) -> str:
        """Validate and normalize the broker symbol."""

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(
                "'symbol' must be a non-empty string."
            )

        return symbol.strip().upper()

    @staticmethod
    def _validate_side(side: Any) -> str:
        """Validate and normalize position side."""

        if not isinstance(side, str) or not side.strip():
            raise ValueError(
                "'side' must be either 'LONG' or 'SHORT'."
            )

        normalized = side.strip().upper()

        if normalized not in {
            "LONG",
            "SHORT",
        }:
            raise ValueError(
                "'side' must be either 'LONG' or 'SHORT'."
            )

        return normalized

    @staticmethod
    def _validate_quantity(quantity: Any) -> int:
        """Validate open-position quantity."""

        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
        ):
            raise ValueError(
                "'quantity' must be a positive integer."
            )

        return quantity

    @staticmethod
    def _validate_signal_id(
        signal_id: Any,
    ) -> str | None:
        """Validate and normalize an optional Eagle signal ID."""

        if signal_id is None:
            return None

        if (
            not isinstance(signal_id, str)
            or not signal_id.strip()
        ):
            raise ValueError(
                "'signal_id' must be a non-empty string "
                "when supplied."
            )

        return signal_id.strip()