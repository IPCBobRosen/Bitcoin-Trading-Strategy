"""Central pre-execution risk checks for BTS trade requests."""

from dataclasses import dataclass
from enum import Enum

from app.communications.protocol import TradeIntent
from app.communications.trade_request import TradeRequest
from app.daily_loss_guard import DailyLossGuard
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls


class RiskDecisionStatus(Enum):
    """Outcome of evaluating one TradeRequest."""

    APPROVED = "Approved"
    REJECTED = "Rejected"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Immutable result of one pre-execution risk evaluation."""

    status: RiskDecisionStatus
    reason: str
    current_position: int
    projected_position: int

    @property
    def approved(self) -> bool:
        """Return True when the trade passed all risk checks."""

        return self.status is RiskDecisionStatus.APPROVED


class RiskManager:
    """Apply broker-independent risk limits before order execution."""

    def __init__(
        self,
        controls: TradingControls,
        kill_switch: KillSwitch,
        daily_loss_guard: DailyLossGuard,
        *,
        allowed_symbols: tuple[str, ...] = ("MBT",),
        max_order_quantity: int = 50,
        max_absolute_position: int = 50,
    ) -> None:
        """Create the BTS risk manager.

        Risk-increasing requests are subject to every configured
        safety gate.

        Valid risk-reducing close requests remain eligible even when
        normal trading is paused, the daily-loss guard has tripped,
        or the emergency kill switch is active. This preserves BTS's
        ability to flatten an existing position safely.
        """

        if not isinstance(
            controls,
            TradingControls,
        ):
            raise TypeError(
                "'controls' must be a TradingControls."
            )

        if not isinstance(
            kill_switch,
            KillSwitch,
        ):
            raise TypeError(
                "'kill_switch' must be a KillSwitch."
            )

        if not isinstance(
            daily_loss_guard,
            DailyLossGuard,
        ):
            raise TypeError(
                "'daily_loss_guard' must be a DailyLossGuard."
            )

        if not isinstance(
            allowed_symbols,
            tuple,
        ):
            raise TypeError(
                "'allowed_symbols' must be a tuple."
            )

        normalized_symbols: list[str] = []

        for symbol in allowed_symbols:
            if (
                not isinstance(symbol, str)
                or not symbol.strip()
            ):
                raise ValueError(
                    "'allowed_symbols' must contain "
                    "non-empty strings."
                )

            normalized_symbol = (
                symbol.strip().upper()
            )

            if normalized_symbol not in normalized_symbols:
                normalized_symbols.append(
                    normalized_symbol
                )

        if not normalized_symbols:
            raise ValueError(
                "'allowed_symbols' must contain at least one symbol."
            )

        if (
            not isinstance(max_order_quantity, int)
            or isinstance(max_order_quantity, bool)
            or max_order_quantity <= 0
        ):
            raise ValueError(
                "'max_order_quantity' must be a positive integer."
            )

        if (
            not isinstance(max_absolute_position, int)
            or isinstance(max_absolute_position, bool)
            or max_absolute_position <= 0
        ):
            raise ValueError(
                "'max_absolute_position' must be a positive integer."
            )

        self._controls = controls
        self._kill_switch = kill_switch
        self._daily_loss_guard = daily_loss_guard

        self._allowed_symbols = tuple(
            normalized_symbols
        )

        self._max_order_quantity = (
            max_order_quantity
        )

        self._max_absolute_position = (
            max_absolute_position
        )

    @property
    def controls(self) -> TradingControls:
        """Return the runtime trading controls."""

        return self._controls

    @property
    def kill_switch(self) -> KillSwitch:
        """Return the emergency trading kill switch."""

        return self._kill_switch

    @property
    def daily_loss_guard(self) -> DailyLossGuard:
        """Return the daily-loss risk control."""

        return self._daily_loss_guard

    @property
    def allowed_symbols(self) -> tuple[str, ...]:
        """Return the normalized symbol allowlist."""

        return self._allowed_symbols

    @property
    def max_order_quantity(self) -> int:
        """Return the maximum contracts allowed per opening request."""

        return self._max_order_quantity

    @property
    def max_absolute_position(self) -> int:
        """Return the maximum absolute resulting position."""

        return self._max_absolute_position

    def evaluate(
        self,
        trade_request: TradeRequest,
        *,
        current_position: int,
    ) -> RiskDecision:
        """Evaluate one TradeRequest against BTS risk limits.

        Position convention:

        Positive position = LONG.
        Negative position = SHORT.
        Zero = FLAT.

        Valid BUY_TO_CLOSE and SELL_TO_CLOSE requests are treated as
        risk-reducing exits and remain available during emergency
        risk states.
        """

        if not isinstance(
            trade_request,
            TradeRequest,
        ):
            raise TypeError(
                "'trade_request' must be a TradeRequest."
            )

        if (
            not isinstance(current_position, int)
            or isinstance(current_position, bool)
        ):
            raise TypeError(
                "'current_position' must be an integer."
            )

        symbol = trade_request.symbol.strip().upper()

        if symbol not in self._allowed_symbols:
            return self._reject(
                reason=(
                    f"Symbol {symbol!r} is not permitted "
                    "by the risk allowlist."
                ),
                current_position=current_position,
            )

        quantity = trade_request.quantity

        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
        ):
            return self._reject(
                reason=(
                    "Trade quantity must be "
                    "a positive integer."
                ),
                current_position=current_position,
            )

        position_delta = self._position_delta(
            trade_request.intent,
            quantity,
        )

        projected_position = (
            current_position
            + position_delta
        )

        if self._is_close_intent(
            trade_request.intent
        ):
            return self._evaluate_close(
                trade_request=trade_request,
                current_position=current_position,
                projected_position=projected_position,
            )

        return self._evaluate_open(
            trade_request=trade_request,
            current_position=current_position,
            projected_position=projected_position,
        )

    def _evaluate_close(
        self,
        *,
        trade_request: TradeRequest,
        current_position: int,
        projected_position: int,
    ) -> RiskDecision:
        """Evaluate a risk-reducing close request.

        Valid closes intentionally bypass:

        - Kill-switch blocking.
        - Daily-loss blocking.
        - TradingControls pause blocking.
        - Normal maximum order quantity.

        They do not bypass close-direction validation and may never
        cross through flat into opposite exposure.
        """

        close_violation = (
            self._validate_close_intent(
                intent=trade_request.intent,
                current_position=current_position,
                projected_position=projected_position,
            )
        )

        if close_violation is not None:
            return RiskDecision(
                status=RiskDecisionStatus.REJECTED,
                reason=close_violation,
                current_position=current_position,
                projected_position=projected_position,
            )

        return RiskDecision(
            status=RiskDecisionStatus.APPROVED,
            reason=(
                "Risk-reducing close request passed "
                "pre-execution safety checks."
            ),
            current_position=current_position,
            projected_position=projected_position,
        )

    def _evaluate_open(
        self,
        *,
        trade_request: TradeRequest,
        current_position: int,
        projected_position: int,
    ) -> RiskDecision:
        """Evaluate a request that creates or adds exposure."""

        if self._kill_switch.active:
            kill_reason = (
                self._kill_switch.reason
                or "No activation reason recorded."
            )

            return self._reject(
                reason=(
                    "Emergency kill switch is active. "
                    f"Reason: {kill_reason}"
                ),
                current_position=current_position,
            )

        if self._daily_loss_guard.tripped:
            return self._reject(
                reason=(
                    "Daily loss limit has been reached. "
                    f"Current daily P&L: "
                    f"{self._daily_loss_guard.total_pnl}. "
                    f"Maximum daily loss: "
                    f"{self._daily_loss_guard.max_daily_loss}."
                ),
                current_position=current_position,
            )

        if self._controls.is_paused:
            return self._reject(
                reason="Trading is paused.",
                current_position=current_position,
            )

        if (
            trade_request.quantity
            > self._max_order_quantity
        ):
            return self._reject(
                reason=(
                    f"Trade quantity {trade_request.quantity} exceeds "
                    f"maximum order quantity "
                    f"{self._max_order_quantity}."
                ),
                current_position=current_position,
            )

        if (
            abs(projected_position)
            > self._max_absolute_position
        ):
            return RiskDecision(
                status=RiskDecisionStatus.REJECTED,
                reason=(
                    f"Projected position "
                    f"{projected_position} exceeds "
                    f"maximum absolute position "
                    f"{self._max_absolute_position}."
                ),
                current_position=current_position,
                projected_position=projected_position,
            )

        return RiskDecision(
            status=RiskDecisionStatus.APPROVED,
            reason=(
                "Trade passed all configured "
                "pre-execution risk checks."
            ),
            current_position=current_position,
            projected_position=projected_position,
        )

    @staticmethod
    def _is_close_intent(
        intent: TradeIntent,
    ) -> bool:
        """Return True for explicit position-closing intents."""

        return intent.value in {
            "BUY_TO_CLOSE",
            "SELL_TO_CLOSE",
        }

    @staticmethod
    def _position_delta(
        intent: TradeIntent,
        quantity: int,
    ) -> int:
        """Convert a trade intent into signed position change."""

        intent_value = intent.value

        if intent_value in {
            "BUY_TO_OPEN",
            "BUY_TO_CLOSE",
        }:
            return quantity

        if intent_value in {
            "SELL_TO_OPEN",
            "SELL_TO_CLOSE",
        }:
            return -quantity

        raise ValueError(
            f"Unsupported TradeIntent: {intent_value!r}."
        )

    @staticmethod
    def _validate_close_intent(
        *,
        intent: TradeIntent,
        current_position: int,
        projected_position: int,
    ) -> str | None:
        """Reject closing requests that are not strictly risk reducing."""

        intent_value = intent.value

        if intent_value == "SELL_TO_CLOSE":
            if current_position <= 0:
                return (
                    "SELL_TO_CLOSE requires an existing "
                    "long position."
                )

            if projected_position < 0:
                return (
                    "SELL_TO_CLOSE quantity would cross "
                    "through flat and create a short position."
                )

        if intent_value == "BUY_TO_CLOSE":
            if current_position >= 0:
                return (
                    "BUY_TO_CLOSE requires an existing "
                    "short position."
                )

            if projected_position > 0:
                return (
                    "BUY_TO_CLOSE quantity would cross "
                    "through flat and create a long position."
                )

        return None

    @staticmethod
    def _reject(
        *,
        reason: str,
        current_position: int,
    ) -> RiskDecision:
        """Create a rejection that does not change position."""

        return RiskDecision(
            status=RiskDecisionStatus.REJECTED,
            reason=reason,
            current_position=current_position,
            projected_position=current_position,
        )