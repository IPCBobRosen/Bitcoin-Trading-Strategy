"""Evaluate whether BTS is safe to resume after an Eagle reconnect."""

from dataclasses import dataclass
from enum import Enum

from app.connection_health import ConnectionHealth
from app.reconciliation_manager import (
    ReconciliationManager,
    ReconciliationStatus,
)
from app.replay_tracker import ReplayTracker


class ReconnectReadinessStatus(Enum):
    """Overall reconnect readiness state."""

    READY = "Ready"
    NOT_READY = "NotReady"


@dataclass(frozen=True, slots=True)
class ReconnectReadinessResult:
    """Outcome of evaluating reconnect safety prerequisites."""

    status: ReconnectReadinessStatus
    reason: str

    @property
    def ready(self) -> bool:
        """Return True when BTS is safe to consider resuming."""

        return self.status is ReconnectReadinessStatus.READY


class ReconnectReadiness:
    """Combine reconnect safety prerequisites into one decision."""

    def __init__(
        self,
        replay_tracker: ReplayTracker,
        reconciliation_manager: ReconciliationManager,
        connection_health: ConnectionHealth,
    ) -> None:
        """Create a reconnect-readiness evaluator."""

        if not isinstance(
            replay_tracker,
            ReplayTracker,
        ):
            raise TypeError(
                "'replay_tracker' must be a ReplayTracker."
            )

        if not isinstance(
            reconciliation_manager,
            ReconciliationManager,
        ):
            raise TypeError(
                "'reconciliation_manager' must be a "
                "ReconciliationManager."
            )

        if not isinstance(
            connection_health,
            ConnectionHealth,
        ):
            raise TypeError(
                "'connection_health' must be a ConnectionHealth."
            )

        self._replay_tracker = replay_tracker
        self._reconciliation_manager = reconciliation_manager
        self._connection_health = connection_health

    def evaluate(self) -> ReconnectReadinessResult:
        """Evaluate whether all reconnect safety conditions are satisfied."""

        if not self._replay_tracker.hello_received:
            return ReconnectReadinessResult(
                status=ReconnectReadinessStatus.NOT_READY,
                reason="Eagle fund.hello has not been received.",
            )

        if not self._replay_tracker.replay_complete:
            return ReconnectReadinessResult(
                status=ReconnectReadinessStatus.NOT_READY,
                reason="Eagle replay has not completed.",
            )

        reconciliation_result = (
            self._reconciliation_manager.last_result
        )

        if (
            reconciliation_result.status
            is not ReconciliationStatus.MATCHED
        ):
            return ReconnectReadinessResult(
                status=ReconnectReadinessStatus.NOT_READY,
                reason="Open-position reconciliation is not matched.",
            )

        if not self._connection_health.is_healthy():
            return ReconnectReadinessResult(
                status=ReconnectReadinessStatus.NOT_READY,
                reason="Eagle heartbeat is not healthy.",
            )

        return ReconnectReadinessResult(
            status=ReconnectReadinessStatus.READY,
            reason="All reconnect safety conditions are satisfied.",
        )