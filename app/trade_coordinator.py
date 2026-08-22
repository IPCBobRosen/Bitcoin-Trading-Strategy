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

    def prepare_event(
        self,
        event: IncomingLifecycleEvent,
    ) -> TradeDecision:
        """Build a TradeRequest without mutating durable lifecycle state.

        This method is intentionally non-mutating so external safety
        layers such as broker-position validation, RiskManager, kill
        switch checks, and broker-readiness checks can run before BTS
        commits an Eagle lifecycle transition.
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

        return TradeDecision(
            approved=True,
            reason=APPROVED,
            trade_request=request,
        )

    def commit_request(
        self,
        request: TradeRequest,
    ) -> TradeDecision:
        """Validate and durably commit one prepared lifecycle request."""

        if not isinstance(
            request,
            TradeRequest,
        ):
            raise TypeError(
                "'request' must be a TradeRequest."
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

    def process_event(
        self,
        event: IncomingLifecycleEvent,
    ) -> TradeDecision:
        """Evaluate one accepted Eagle event and return a trade decision.

        This compatibility method preserves the original coordinator
        behavior for existing callers:

        1. Trading must be enabled.
        2. Build the BTS TradeRequest.
        3. Validate and commit the Eagle signal lifecycle transition.
        4. Approve only when lifecycle state is valid.

        Safety-critical callers that must perform external risk or broker
        checks before lifecycle mutation should call prepare_event(),
        perform those checks, and then call commit_request().
        """

        prepared = self.prepare_event(
            event
        )

        if not prepared.approved:
            return prepared

        request = prepared.trade_request

        if request is None:
            raise RuntimeError(
                "Approved prepared decision had no TradeRequest."
            )

        return self.commit_request(
            request
        )