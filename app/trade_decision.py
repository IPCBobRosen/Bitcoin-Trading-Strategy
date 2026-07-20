"""Represents the outcome of processing one Eagle lifecycle event."""

from dataclasses import dataclass

from app.communications.trade_request import TradeRequest


@dataclass(frozen=True, slots=True)
class TradeDecision:
    """The result of evaluating one Eagle signal."""

    approved: bool
    reason: str
    trade_request: TradeRequest | None = None