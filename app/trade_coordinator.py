"""Coordinate accepted Eagle events with BTS trading controls."""

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.trade_request import TradeRequest
from app.trade_decision import TradeDecision
from app.trading_controls import TradingControls


APPROVED = "Approved"
TRADING_PAUSED = "TradingPaused"


class TradeCoordinator:
    """Evaluate accepted Eagle events against BTS trading controls."""

    def __init__(
        self,
        controls: TradingControls,
    ) -> None:
        """Create a coordinator using the supplied trading controls."""

        self._controls = controls

    def process_event(
        self,
        event: IncomingLifecycleEvent,
    ) -> TradeDecision:
        """Evaluate one accepted Eagle event and return a trade decision."""

        if self._controls.is_paused:
            return TradeDecision(
                approved=False,
                reason=TRADING_PAUSED,
            )

        settings = self._controls.create_snapshot()

        request = TradeRequest.from_event(
            event=event,
            settings=settings,
        )

        return TradeDecision(
            approved=True,
            reason=APPROVED,
            trade_request=request,
        )