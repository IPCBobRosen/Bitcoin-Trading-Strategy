"""Communication protocol definitions for the Bitcoin Trading System.

This module defines the trade instructions that the machine-learning computer
is allowed to send to BTS. The ML computer expresses trade intent, while BTS
determines quantity, validates risk, and manages order execution.
"""

"""Communication protocol definitions for the Bitcoin Trading System."""

from enum import Enum


class TradeIntent(str, Enum):
    """Supported trade intentions used internally by BTS."""

    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"


class Environment(str, Enum):
    """Environments allowed by the IPC Signal API."""

    STAGING = "staging"
    LIVE = "live"