"""Safely control whether BTS may resume trading."""

from dataclasses import dataclass
from enum import Enum

from app.reconnect_readiness import ReconnectReadiness
from app.trading_controls import TradingControls


class ResumeStatus(Enum):
    """Result of a request to resume BTS trading."""

    RESUMED = "Resumed"
    REJECTED = "Rejected"


@dataclass(frozen=True, slots=True)
class ResumeResult:
    """Outcome of one trading-resume request."""

    status: ResumeStatus
    reason: str

    @property
    def resumed(self) -> bool:
        """Return True when trading was successfully resumed."""

        return self.status is ResumeStatus.RESUMED


class ResumeManager:
    """Permit trading resume only when reconnect safety is satisfied."""

    def __init__(
        self,
        controls: TradingControls,
        reconnect_readiness: ReconnectReadiness,
    ) -> None:
        """Create the resume manager."""

        if not isinstance(
            controls,
            TradingControls,
        ):
            raise TypeError(
                "'controls' must be a TradingControls."
            )

        if not isinstance(
            reconnect_readiness,
            ReconnectReadiness,
        ):
            raise TypeError(
                "'reconnect_readiness' must be a ReconnectReadiness."
            )

        self._controls = controls
        self._reconnect_readiness = reconnect_readiness

    def request_resume(self) -> ResumeResult:
        """Attempt to resume trading.

        Trading may resume only when all reconnect-readiness
        conditions are currently satisfied.

        A rejected resume request leaves trading paused.
        """

        readiness_result = (
            self._reconnect_readiness.evaluate()
        )

        if not readiness_result.ready:
            self._controls.pause()

            return ResumeResult(
                status=ResumeStatus.REJECTED,
                reason=(
                    "Trading resume rejected. "
                    f"{readiness_result.reason}"
                ),
            )

        self._controls.resume()

        return ResumeResult(
            status=ResumeStatus.RESUMED,
            reason=(
                "Trading resumed because all reconnect "
                "safety conditions are satisfied."
            ),
        )