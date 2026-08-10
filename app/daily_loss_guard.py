"""Track BTS daily P&L and enforce a maximum daily loss."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class DailyLossSnapshot:
    """Immutable snapshot of the current daily-loss state."""

    max_daily_loss: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    tripped: bool


class DailyLossGuard:
    """Block new risk after the configured daily loss is reached."""

    def __init__(
        self,
        max_daily_loss: Decimal | int | float | str,
    ) -> None:
        """Create a daily-loss guard.

        Args:
            max_daily_loss:
                Positive dollar loss magnitude.

                For example, ``5000`` means the guard trips when
                combined realized and unrealized daily P&L reaches
                -5000 or worse.
        """

        self._max_daily_loss = self._validate_positive_decimal(
            max_daily_loss,
            "max_daily_loss",
        )

        self._realized_pnl = Decimal("0")
        self._unrealized_pnl = Decimal("0")
        self._tripped = False

    @property
    def max_daily_loss(self) -> Decimal:
        """Return the configured positive loss magnitude."""

        return self._max_daily_loss

    @property
    def realized_pnl(self) -> Decimal:
        """Return current realized daily P&L."""

        return self._realized_pnl

    @property
    def unrealized_pnl(self) -> Decimal:
        """Return current unrealized daily P&L."""

        return self._unrealized_pnl

    @property
    def total_pnl(self) -> Decimal:
        """Return combined realized and unrealized daily P&L."""

        return (
            self._realized_pnl
            + self._unrealized_pnl
        )

    @property
    def tripped(self) -> bool:
        """Return True once the daily loss limit has been breached."""

        return self._tripped

    @property
    def remaining_loss_capacity(self) -> Decimal:
        """Return remaining loss capacity before the threshold.

        Zero is returned once the guard has tripped.
        """

        if self._tripped:
            return Decimal("0")

        remaining = (
            self._max_daily_loss
            + self.total_pnl
        )

        if remaining <= 0:
            return Decimal("0")

        return remaining

    def update_realized_pnl(
        self,
        value: Decimal | int | float | str,
    ) -> None:
        """Replace the current realized daily P&L value."""

        self._realized_pnl = self._validate_pnl(
            value,
            "realized_pnl",
        )

        self._evaluate_limit()

    def update_unrealized_pnl(
        self,
        value: Decimal | int | float | str,
    ) -> None:
        """Replace the current unrealized daily P&L value."""

        self._unrealized_pnl = self._validate_pnl(
            value,
            "unrealized_pnl",
        )

        self._evaluate_limit()

    def update_pnl(
        self,
        *,
        realized_pnl: Decimal | int | float | str,
        unrealized_pnl: Decimal | int | float | str,
    ) -> None:
        """Atomically replace realized and unrealized daily P&L."""

        new_realized = self._validate_pnl(
            realized_pnl,
            "realized_pnl",
        )

        new_unrealized = self._validate_pnl(
            unrealized_pnl,
            "unrealized_pnl",
        )

        self._realized_pnl = new_realized
        self._unrealized_pnl = new_unrealized

        self._evaluate_limit()

    def reset_day(self) -> None:
        """Reset P&L and trip state for a new trading day."""

        self._realized_pnl = Decimal("0")
        self._unrealized_pnl = Decimal("0")
        self._tripped = False

    def snapshot(self) -> DailyLossSnapshot:
        """Return an immutable daily-loss snapshot."""

        return DailyLossSnapshot(
            max_daily_loss=self._max_daily_loss,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self._unrealized_pnl,
            total_pnl=self.total_pnl,
            tripped=self._tripped,
        )

    def _evaluate_limit(self) -> None:
        """Trip permanently for the session once loss limit is reached."""

        if self._tripped:
            return

        loss_threshold = -self._max_daily_loss

        if self.total_pnl <= loss_threshold:
            self._tripped = True

    @staticmethod
    def _validate_positive_decimal(
        value: Any,
        field_name: str,
    ) -> Decimal:
        """Validate a finite positive Decimal-compatible value."""

        if isinstance(
            value,
            bool,
        ):
            raise ValueError(
                f"'{field_name}' must be a positive number."
            )

        try:
            normalized = Decimal(
                str(value)
            )

        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            raise ValueError(
                f"'{field_name}' must be a positive number."
            ) from error

        if (
            not normalized.is_finite()
            or normalized <= 0
        ):
            raise ValueError(
                f"'{field_name}' must be a positive number."
            )

        return normalized

    @staticmethod
    def _validate_pnl(
        value: Any,
        field_name: str,
    ) -> Decimal:
        """Validate a finite signed P&L value."""

        if isinstance(
            value,
            bool,
        ):
            raise ValueError(
                f"'{field_name}' must be a finite number."
            )

        try:
            normalized = Decimal(
                str(value)
            )

        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            raise ValueError(
                f"'{field_name}' must be a finite number."
            ) from error

        if not normalized.is_finite():
            raise ValueError(
                f"'{field_name}' must be a finite number."
            )

        return normalized