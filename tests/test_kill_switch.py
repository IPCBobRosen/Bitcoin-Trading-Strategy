"""Tests for the BTS emergency trading kill switch."""

from datetime import datetime, timezone

import pytest

from app.kill_switch import (
    KillSwitch,
    KillSwitchSnapshot,
)


def test_new_kill_switch_is_inactive() -> None:
    """A new kill switch should permit normal evaluation."""

    kill_switch = KillSwitch()

    assert kill_switch.active is False


def test_new_kill_switch_has_no_reason() -> None:
    """Inactive kill switch should have no activation reason."""

    kill_switch = KillSwitch()

    assert kill_switch.reason is None


def test_new_kill_switch_has_no_activation_time() -> None:
    """Inactive kill switch should have no activation timestamp."""

    kill_switch = KillSwitch()

    assert kill_switch.activated_at is None


def test_activate_sets_active_state() -> None:
    """Activation should immediately trip the kill switch."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Emergency operator stop."
    )

    assert kill_switch.active is True


def test_activate_records_reason() -> None:
    """Activation should retain an audit-friendly reason."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Position mismatch detected."
    )

    assert (
        kill_switch.reason
        == "Position mismatch detected."
    )


def test_activate_strips_reason_whitespace() -> None:
    """Activation reason should be normalized."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "  Broker disconnected.  "
    )

    assert (
        kill_switch.reason
        == "Broker disconnected."
    )


def test_activate_records_utc_timestamp() -> None:
    """Activation should record a timezone-aware UTC time."""

    kill_switch = KillSwitch()

    before = datetime.now(
        timezone.utc
    )

    kill_switch.activate(
        "Emergency stop."
    )

    after = datetime.now(
        timezone.utc
    )

    assert kill_switch.activated_at is not None

    assert (
        before
        <= kill_switch.activated_at
        <= after
    )

    assert (
        kill_switch.activated_at.tzinfo
        is not None
    )


def test_empty_reason_is_rejected() -> None:
    """Kill-switch activation requires a reason."""

    kill_switch = KillSwitch()

    with pytest.raises(
        ValueError,
        match="'reason' must be a non-empty string",
    ):
        kill_switch.activate(
            ""
        )

    assert kill_switch.active is False


def test_whitespace_reason_is_rejected() -> None:
    """Whitespace alone is not a valid activation reason."""

    kill_switch = KillSwitch()

    with pytest.raises(
        ValueError,
        match="'reason' must be a non-empty string",
    ):
        kill_switch.activate(
            "   "
        )

    assert kill_switch.active is False


def test_non_string_reason_is_rejected() -> None:
    """Activation reason must be textual."""

    kill_switch = KillSwitch()

    with pytest.raises(
        ValueError,
        match="'reason' must be a non-empty string",
    ):
        kill_switch.activate(
            123  # type: ignore[arg-type]
        )

    assert kill_switch.active is False


def test_repeated_activation_preserves_first_reason() -> None:
    """First emergency reason should not be overwritten."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "First emergency."
    )

    kill_switch.activate(
        "Second emergency."
    )

    assert (
        kill_switch.reason
        == "First emergency."
    )


def test_repeated_activation_preserves_first_timestamp() -> None:
    """Repeated activation should preserve original trip time."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "First emergency."
    )

    first_timestamp = (
        kill_switch.activated_at
    )

    kill_switch.activate(
        "Second emergency."
    )

    assert (
        kill_switch.activated_at
        == first_timestamp
    )


def test_reset_clears_active_state() -> None:
    """Explicit reset should clear the emergency state."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Emergency."
    )

    kill_switch.reset()

    assert kill_switch.active is False


def test_reset_clears_reason() -> None:
    """Reset should clear the previous emergency reason."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Emergency."
    )

    kill_switch.reset()

    assert kill_switch.reason is None


def test_reset_clears_activation_time() -> None:
    """Reset should clear the previous activation timestamp."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Emergency."
    )

    kill_switch.reset()

    assert kill_switch.activated_at is None


def test_switch_can_be_activated_again_after_reset() -> None:
    """Reset switch should support a later new emergency."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "First emergency."
    )

    kill_switch.reset()

    kill_switch.activate(
        "Second emergency."
    )

    assert kill_switch.active is True
    assert kill_switch.reason == "Second emergency."


def test_inactive_snapshot_is_correct() -> None:
    """Snapshot should represent an inactive switch."""

    kill_switch = KillSwitch()

    result = kill_switch.snapshot()

    assert result == KillSwitchSnapshot(
        active=False,
        reason=None,
        activated_at=None,
    )


def test_active_snapshot_is_correct() -> None:
    """Snapshot should preserve active emergency state."""

    kill_switch = KillSwitch()

    kill_switch.activate(
        "Risk limit breached."
    )

    result = kill_switch.snapshot()

    assert result.active is True
    assert result.reason == "Risk limit breached."
    assert result.activated_at == kill_switch.activated_at


def test_snapshot_is_immutable() -> None:
    """Kill-switch snapshots must not be mutable."""

    kill_switch = KillSwitch()

    result = kill_switch.snapshot()

    with pytest.raises(
        AttributeError,
    ):
        result.active = True  # type: ignore[misc]