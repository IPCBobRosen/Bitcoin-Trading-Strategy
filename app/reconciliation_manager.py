"""Compare Eagle open-position state with the local broker position state."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


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
        broker_positions: list[dict[str, Any]]
        | tuple[dict[str, Any], ...],
    ) -> ReconciliationResult:
        """Compare Eagle and broker open-position snapshots.

        The first version performs deterministic structural comparison.

        Position ordering does not matter. Each position dictionary must
        match exactly after normalization.

        More broker-specific normalization can be added later when the
        Interactive Brokers position schema is connected.
        """

        normalized_eagle = self._normalize_positions(
            eagle_positions,
            "eagle_positions",
        )

        normalized_broker = self._normalize_positions(
            broker_positions,
            "broker_positions",
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
    def _normalize_positions(
        positions: list[dict[str, Any]]
        | tuple[dict[str, Any], ...],
        field_name: str,
    ) -> tuple[dict[str, Any], ...]:
        """Validate and copy one position collection."""

        if not isinstance(
            positions,
            (list, tuple),
        ):
            raise TypeError(
                f"'{field_name}' must be a list or tuple."
            )

        normalized: list[dict[str, Any]] = []

        for position in positions:
            if not isinstance(
                position,
                dict,
            ):
                raise TypeError(
                    f"'{field_name}' must contain only dictionaries."
                )

            normalized.append(
                dict(position)
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