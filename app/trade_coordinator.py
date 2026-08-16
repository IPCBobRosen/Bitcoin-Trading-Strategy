"""Coordinate accepted Eagle events with BTS trading controls."""

from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.trade_request import TradeRequest
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleStatus,
)
from app.trade_decision import TradeDecision
from app.trading_controls import TradingControls


APPROVED = "Approved"
TRADING_PAUSED = "TradingPaused"
INVALID_SIGNAL_LIFECYCLE = "InvalidSignalLifecycle"


class TradeCoordinator:
    """Evaluate accepted Eagle events against BTS trading controls."""

    def __init__(
        self,
        controls: TradingControls,
        signal_lifecycle_guard: SignalLifecycleGuard,
    ) -> None:
        """Create a coordinator using trading and lifecycle controls."""

        if not isinstance(
            controls,
            TradingControls,
        ):
            raise TypeError(
                "'controls' must be a TradingControls."
            )

        if not isinstance(
            signal_lifecycle_guard,
            SignalLifecycleGuard,
        ):
            raise TypeError(
                "'signal_lifecycle_guard' must be a "
                "SignalLifecycleGuard."
            )

        self._controls = controls
        self._signal_lifecycle_guard = signal_lifecycle_guard

    @property
    def controls(self) -> TradingControls:
        """Return the configured trading controls."""

        return self._controls

    @property
    def signal_lifecycle_guard(
        self,
    ) -> SignalLifecycleGuard:
        """Return the durable signal lifecycle guard."""

        return self._signal_lifecycle_guard

    def process_event(
        self,
        event: IncomingLifecycleEvent,
    ) -> TradeDecision:
        """Evaluate one accepted Eagle event and return a trade decision.

        Processing order:

        1. Trading must be enabled.
        2. Build the BTS TradeRequest.
        3. Validate the Eagle signal lifecycle transition.
        4. Approve only when lifecycle state is valid.
        """

        if not isinstance(
            event,
            IncomingLifecycleEvent,
        ):
            raise TypeError(
                "'event' must be an IncomingLifecycleEvent."
            )

        if self._controls.is_paused:
            return TradeDecision(
                approved=False,
                reason=TRADING_PAUSED,
            )

        settings = (
            self._controls.create_snapshot()
        )

        request = TradeRequest.from_event(
            event=event,
            settings=settings,
        )

        lifecycle_decision = (
            self._signal_lifecycle_guard.process(
                request
            )
        )

        if (
            lifecycle_decision.status
            is SignalLifecycleStatus.INVALID_TRANSITION
        ):
            return TradeDecision(
                approved=False,
                reason=INVALID_SIGNAL_LIFECYCLE,
            )

        return TradeDecision(
            approved=True,
            reason=APPROVED,
            trade_request=request,
        )