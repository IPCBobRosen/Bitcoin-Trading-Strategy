"""Compare Eagle open-position state with the local broker position state."""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.broker_position import BrokerPosition


class ReconciliationStatus(Enum):
    """Result of comparing Eagle and broker open-position state."""

    NOT_CHECKED = "NotChecked"
    MATCHED = "Matched"
    MISMATCHED = "Mismatched"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of one open-position reconciliation check."""

    status: ReconciliationStatus
    reason: str
    eagle_positions: tuple[dict[str, Any], ...]
    broker_positions: tuple[dict[str, Any], ...]

    @property
    def matched(self) -> bool:
        """Return True when Eagle and broker positions agree."""

        return self.status is ReconciliationStatus.MATCHED


class ReconciliationManager:
    """Compare Eagle and broker open-position snapshots."""

    def __init__(self) -> None:
        """Create a reconciliation manager that has not yet checked state."""

        self._last_result = ReconciliationResult(
            status=ReconciliationStatus.NOT_CHECKED,
            reason="Reconciliation has not yet been performed.",
            eagle_positions=(),
            broker_positions=(),
        )

    @property
    def last_result(self) -> ReconciliationResult:
        """Return the most recent reconciliation result."""

        return self._last_result

    def reconcile(
        self,
        *,
        eagle_positions: list[dict[str, Any]]
        | tuple[dict[str, Any], ...],
        broker_positions: list[dict[str, Any] | BrokerPosition]
        | tuple[dict[str, Any] | BrokerPosition, ...],
    ) -> ReconciliationResult:
        """Compare Eagle and broker open-position snapshots.

        Eagle positions are represented as dictionaries.

        Broker positions may be supplied either as reconciliation
        dictionaries or normalized BrokerPosition objects.

        Position ordering does not matter.
        """

        normalized_eagle = self._normalize_eagle_positions(
            eagle_positions
        )

        normalized_broker = self._normalize_broker_positions(
            broker_positions
        )

        eagle_sorted = self._sort_positions(
            normalized_eagle
        )

        broker_sorted = self._sort_positions(
            normalized_broker
        )

        if eagle_sorted == broker_sorted:
            result = ReconciliationResult(
                status=ReconciliationStatus.MATCHED,
                reason="Eagle and broker open-position state match.",
                eagle_positions=normalized_eagle,
                broker_positions=normalized_broker,
            )

        else:
            result = ReconciliationResult(
                status=ReconciliationStatus.MISMATCHED,
                reason=(
                    "Eagle and broker open-position state do not match."
                ),
                eagle_positions=normalized_eagle,
                broker_positions=normalized_broker,
            )

        self._last_result = result

        return result

    @staticmethod
    def _normalize_eagle_positions(
        positions: list[dict[str, Any]]
        | tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        """Validate and copy Eagle position dictionaries."""

        if not isinstance(
            positions,
            (list, tuple),
        ):
            raise TypeError(
                "'eagle_positions' must be a list or tuple."
            )

        normalized: list[dict[str, Any]] = []

        for position in positions:
            if not isinstance(
                position,
                dict,
            ):
                raise TypeError(
                    "'eagle_positions' must contain only dictionaries."
                )

            normalized.append(
                dict(position)
            )

        return tuple(
            normalized
        )

    @staticmethod
    def _normalize_broker_positions(
        positions: list[dict[str, Any] | BrokerPosition]
        | tuple[dict[str, Any] | BrokerPosition, ...],
    ) -> tuple[dict[str, Any], ...]:
        """Normalize broker dictionaries and BrokerPosition objects."""

        if not isinstance(
            positions,
            (list, tuple),
        ):
            raise TypeError(
                "'broker_positions' must be a list or tuple."
            )

        normalized: list[dict[str, Any]] = []

        for position in positions:
            if isinstance(
                position,
                BrokerPosition,
            ):
                normalized.append(
                    position.to_dict()
                )
                continue

            if isinstance(
                position,
                dict,
            ):
                normalized.append(
                    dict(position)
                )
                continue

            raise TypeError(
                "'broker_positions' must contain only dictionaries "
                "or BrokerPosition objects."
            )

        return tuple(
            normalized
        )

    @staticmethod
    def _sort_positions(
        positions: tuple[dict[str, Any], ...],
    ) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Create a deterministic representation for comparison."""

        normalized_rows: list[
            tuple[tuple[str, str], ...]
        ] = []

        for position in positions:
            normalized_rows.append(
                tuple(
                    sorted(
                        (
                            str(key),
                            repr(value),
                        )
                        for key, value in position.items()
                    )
                )
            )

        return tuple(
            sorted(
                normalized_rows
            )
        )