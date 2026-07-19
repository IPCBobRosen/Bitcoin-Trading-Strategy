"""Trade requests created from validated Eagle lifecycle events."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import Environment, TradeIntent
from app.trading_controls import TradingSettingsSnapshot


@dataclass(frozen=True, slots=True)
class TradeRequest:
    """An immutable request for BTS to evaluate as a potential trade.

    A TradeRequest combines:

    1. The trade intent received from Eagle.
    2. The trader-controlled settings captured when the request is created.

    This object is not yet a CQG order. It must still pass risk checks,
    position checks, duplicate checks, and other execution safeguards.
    """

    event_id: str
    signal_id: str
    timestamp: datetime
    environment: Environment
    intent: TradeIntent
    symbol: str
    quantity: int
    stop_loss_points: Decimal

    @classmethod
    def from_event(
        cls,
        event: IncomingLifecycleEvent,
        settings: TradingSettingsSnapshot,
    ) -> "TradeRequest":
        """Create a trade request from an event and settings snapshot.

        Args:
            event:
                A validated lifecycle event received from Eagle.

            settings:
                An immutable snapshot of the trader's current symbol,
                quantity, and stop-loss settings.

        Returns:
            An immutable TradeRequest.

        Raises:
            TypeError:
                If event or settings has the wrong object type.

            ValueError:
                If the event payload is missing a valid trade intent.
        """

        if not isinstance(event, IncomingLifecycleEvent):
            raise TypeError(
                "'event' must be an IncomingLifecycleEvent."
            )

        if not isinstance(settings, TradingSettingsSnapshot):
            raise TypeError(
                "'settings' must be a TradingSettingsSnapshot."
            )

        raw_intent = event.payload.get("intent")

        if not isinstance(raw_intent, str):
            raise ValueError(
                "Event payload must contain a string 'intent'."
            )

        try:
            intent = TradeIntent(raw_intent)
        except ValueError as error:
            allowed_intents = ", ".join(
                trade_intent.value
                for trade_intent in TradeIntent
            )

            raise ValueError(
                f"Unsupported trade intent: {raw_intent!r}. "
                f"Allowed values: {allowed_intents}."
            ) from error

        return cls(
            event_id=event.event_id,
            signal_id=event.signal_id,
            timestamp=event.timestamp,
            environment=event.environment,
            intent=intent,
            symbol=settings.symbol,
            quantity=settings.quantity,
            stop_loss_points=settings.stop_loss_points,
        )