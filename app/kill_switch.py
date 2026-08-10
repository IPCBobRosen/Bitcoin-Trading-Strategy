"""Emergency trading kill switch for BTS."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class KillSwitchSnapshot:
    """Immutable snapshot of the BTS kill-switch state."""

    active: bool
    reason: str | None
    activated_at: datetime | None


class KillSwitch:
    """Emergency control that blocks new trading activity."""

    def __init__(self) -> None:
        """Create an inactive kill switch."""

        self._active = False
        self._reason: str | None = None
        self._activated_at: datetime | None = None

    @property
    def active(self) -> bool:
        """Return True when the kill switch is active."""

        return self._active

    @property
    def reason(self) -> str | None:
        """Return the reason for the current activation."""

        return self._reason

    @property
    def activated_at(self) -> datetime | None:
        """Return when the current activation occurred."""

        return self._activated_at

    def activate(
        self,
        reason: str,
    ) -> None:
        """Activate the emergency trading kill switch.

        Once active, repeated activation does not overwrite the
        original reason or activation time. This preserves the
        first cause of the emergency for audit purposes.
        """

        if (
            not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError(
                "'reason' must be a non-empty string."
            )

        if self._active:
            return

        self._active = True
        self._reason = reason.strip()
        self._activated_at = datetime.now(
            timezone.utc
        )

    def reset(self) -> None:
        """Explicitly reset the kill switch.

        Resetting permits the surrounding risk system to evaluate
        new trades again. It does not itself resume TradingControls.
        """

        self._active = False
        self._reason = None
        self._activated_at = None

    def snapshot(self) -> KillSwitchSnapshot:
        """Return an immutable snapshot of kill-switch state."""

        return KillSwitchSnapshot(
            active=self._active,
            reason=self._reason,
            activated_at=self._activated_at,
        )