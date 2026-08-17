"""Translate real Eagle lifecycle messages into BTS trade intent.

This module is intentionally narrow.

Responsibilities:

1. Accept a validated IncomingLifecycleEvent.
2. For fund.entry:
   - inspect the preserved Eagle signal object
   - allow BTCUSDT only
   - map long -> BUY_TO_OPEN
   - map short -> SELL_TO_OPEN
3. For fund.exit:
   - use durable SignalLifecycleGuard state
   - LONG_OPEN -> SELL_TO_CLOSE
   - SHORT_OPEN -> BUY_TO_CLOSE
4. Ignore unsupported instruments and exits with no known open lifecycle.
5. Return a normalized IncomingLifecycleEvent containing payload["intent"].

This module does not submit orders and does not communicate with a broker.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import TradeIntent
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)


SUPPORTED_EAGLE_SYMBOL = "BTCUSDT"


class EagleTradeAdaptStatus(str, Enum):
    """Outcome of adapting one Eagle lifecycle event."""

    ADAPTED = "Adapted"
    IGNORED_SYMBOL = "IgnoredSymbol"
    IGNORED_UNKNOWN_EXIT = "IgnoredUnknownExit"


@dataclass(frozen=True, slots=True)
class EagleTradeAdaptResult:
    """Immutable result of one Eagle-to-BTS adaptation."""

    status: EagleTradeAdaptStatus
    event: IncomingLifecycleEvent | None
    eagle_symbol: str | None
    reason: str

    @property
    def adapted(self) -> bool:
        """Return True when an event is safe for BTS decision processing."""

        return (
            self.status
            is EagleTradeAdaptStatus.ADAPTED
        )


class EagleTradeAdapter:
    """Translate real Eagle fund lifecycle frames into BTS intent."""

    def __init__(
        self,
        lifecycle_guard: SignalLifecycleGuard,
    ) -> None:
        """Create an adapter using durable signal lifecycle state."""

        if not isinstance(
            lifecycle_guard,
            SignalLifecycleGuard,
        ):
            raise TypeError(
                "'lifecycle_guard' must be a "
                "SignalLifecycleGuard."
            )

        self._lifecycle_guard = lifecycle_guard

    @property
    def lifecycle_guard(
        self,
    ) -> SignalLifecycleGuard:
        """Return the configured durable lifecycle guard."""

        return self._lifecycle_guard

    def adapt(
        self,
        event: IncomingLifecycleEvent,
    ) -> EagleTradeAdaptResult:
        """Translate one real Eagle lifecycle event."""

        if not isinstance(
            event,
            IncomingLifecycleEvent,
        ):
            raise TypeError(
                "'event' must be an IncomingLifecycleEvent."
            )

        if event.message_type == "fund.entry":
            return self._adapt_entry(
                event
            )

        if event.message_type == "fund.exit":
            return self._adapt_exit(
                event
            )

        raise ValueError(
            "Unsupported Eagle lifecycle message type: "
            f"{event.message_type!r}."
        )

    def _adapt_entry(
        self,
        event: IncomingLifecycleEvent,
    ) -> EagleTradeAdaptResult:
        """Translate one Eagle fund.entry frame."""

        raw_signal = event.payload.get(
            "signal"
        )

        if not isinstance(
            raw_signal,
            Mapping,
        ):
            raise ValueError(
                "fund.entry must contain a signal object."
            )

        raw_symbol = raw_signal.get(
            "symbol"
        )

        if (
            not isinstance(raw_symbol, str)
            or not raw_symbol.strip()
        ):
            raise ValueError(
                "fund.entry signal must contain "
                "a non-empty 'symbol'."
            )

        eagle_symbol = (
            raw_symbol.strip().upper()
        )

        if eagle_symbol != SUPPORTED_EAGLE_SYMBOL:
            return EagleTradeAdaptResult(
                status=(
                    EagleTradeAdaptStatus.IGNORED_SYMBOL
                ),
                event=None,
                eagle_symbol=eagle_symbol,
                reason=(
                    "Eagle symbol is not enabled in "
                    "the BTC-only BTS configuration."
                ),
            )

        raw_direction = raw_signal.get(
            "direction"
        )

        if (
            not isinstance(raw_direction, str)
            or not raw_direction.strip()
        ):
            raise ValueError(
                "fund.entry signal must contain "
                "a non-empty 'direction'."
            )

        direction = (
            raw_direction.strip().lower()
        )

        if direction == "long":
            intent = TradeIntent.BUY_TO_OPEN

        elif direction == "short":
            intent = TradeIntent.SELL_TO_OPEN

        else:
            raise ValueError(
                "Unsupported Eagle entry direction: "
                f"{raw_direction!r}."
            )

        normalized_event = (
            self._with_intent(
                event=event,
                intent=intent,
            )
        )

        return EagleTradeAdaptResult(
            status=(
                EagleTradeAdaptStatus.ADAPTED
            ),
            event=normalized_event,
            eagle_symbol=eagle_symbol,
            reason=(
                "BTCUSDT Eagle entry translated "
                "into BTS trade intent."
            ),
        )

    def _adapt_exit(
        self,
        event: IncomingLifecycleEvent,
    ) -> EagleTradeAdaptResult:
        """Translate one Eagle fund.exit using durable lifecycle state."""

        lifecycle_state = (
            self._lifecycle_guard.get_state(
                event.signal_id
            )
        )

        if lifecycle_state is None:
            return EagleTradeAdaptResult(
                status=(
                    EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
                ),
                event=None,
                eagle_symbol=None,
                reason=(
                    "Eagle exit has no known open BTS "
                    "signal lifecycle."
                ),
            )

        if (
            lifecycle_state
            is SignalLifecycleState.LONG_OPEN
        ):
            intent = TradeIntent.SELL_TO_CLOSE

        elif (
            lifecycle_state
            is SignalLifecycleState.SHORT_OPEN
        ):
            intent = TradeIntent.BUY_TO_CLOSE

        else:
            return EagleTradeAdaptResult(
                status=(
                    EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
                ),
                event=None,
                eagle_symbol=None,
                reason=(
                    "Eagle exit does not correspond to "
                    "an open BTS signal lifecycle."
                ),
            )

        normalized_event = (
            self._with_intent(
                event=event,
                intent=intent,
            )
        )

        return EagleTradeAdaptResult(
            status=(
                EagleTradeAdaptStatus.ADAPTED
            ),
            event=normalized_event,
            eagle_symbol=SUPPORTED_EAGLE_SYMBOL,
            reason=(
                "Eagle exit translated from durable "
                "BTS lifecycle state."
            ),
        )

    @staticmethod
    def _with_intent(
        *,
        event: IncomingLifecycleEvent,
        intent: TradeIntent,
    ) -> IncomingLifecycleEvent:
        """Return a copy of event containing normalized BTS intent."""

        payload: dict[str, Any] = dict(
            event.payload
        )

        payload["intent"] = (
            intent.value
        )

        return IncomingLifecycleEvent(
            message_type=event.message_type,
            seq=event.seq,
            event_id=event.event_id,
            signal_id=event.signal_id,
            timestamp=event.timestamp,
            environment=event.environment,
            payload=payload,
        )