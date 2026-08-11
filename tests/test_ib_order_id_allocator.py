"""Tests for the BTS Interactive Brokers order-ID allocator."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.ib_order_id_allocator import (
    IBOrderIdAllocator,
    IBOrderIdSnapshot,
)


def test_new_allocator_is_not_initialized() -> None:
    """New allocator should wait for IB nextValidId."""

    allocator = IBOrderIdAllocator()

    assert allocator.initialized is False


def test_new_allocator_has_no_next_order_id() -> None:
    """Uninitialized allocator should expose no next ID."""

    allocator = IBOrderIdAllocator()

    assert allocator.next_order_id is None


def test_new_allocator_has_no_allocated_id() -> None:
    """New allocator should have no allocation history."""

    allocator = IBOrderIdAllocator()

    assert (
        allocator.highest_allocated_order_id
        is None
    )


def test_new_allocator_has_no_observed_id() -> None:
    """New allocator should have no IB observation history."""

    allocator = IBOrderIdAllocator()

    assert (
        allocator.highest_observed_order_id
        is None
    )


def test_allocate_before_initialization_is_rejected() -> None:
    """BTS must not allocate before nextValidId arrives."""

    allocator = IBOrderIdAllocator()

    with pytest.raises(
        RuntimeError,
        match="not initialized",
    ):
        allocator.allocate()


def test_initialize_sets_allocator_ready() -> None:
    """nextValidId should initialize allocation."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    assert allocator.initialized is True
    assert allocator.next_order_id == 100


def test_first_allocation_uses_next_valid_id() -> None:
    """First allocation should use IB supplied ID exactly."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    assert allocator.allocate() == 100


def test_allocation_advances_sequence() -> None:
    """Each allocated ID should advance by one."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    assert allocator.allocate() == 100
    assert allocator.allocate() == 101
    assert allocator.allocate() == 102


def test_next_order_id_advances_after_allocation() -> None:
    """Allocator should expose the upcoming ID."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.allocate()

    assert allocator.next_order_id == 101


def test_highest_allocated_id_is_recorded() -> None:
    """Allocator should remember highest ID handed to BTS."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.allocate()
    allocator.allocate()

    assert (
        allocator.highest_allocated_order_id
        == 101
    )


def test_reinitialize_with_higher_id_advances_allocator() -> None:
    """A later higher IB nextValidId should advance floor."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.initialize(
        200
    )

    assert allocator.next_order_id == 200


def test_reinitialize_with_lower_id_does_not_regress() -> None:
    """A stale lower nextValidId must never move BTS backward."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.allocate()
    allocator.allocate()

    assert allocator.next_order_id == 102

    allocator.initialize(
        50
    )

    assert allocator.next_order_id == 102


def test_observed_order_id_is_recorded() -> None:
    """IB callback order IDs should be remembered."""

    allocator = IBOrderIdAllocator()

    allocator.observe_order_id(
        250
    )

    assert (
        allocator.highest_observed_order_id
        == 250
    )


def test_observation_before_initialization_does_not_initialize() -> None:
    """Observed callbacks alone must not permit order allocation."""

    allocator = IBOrderIdAllocator()

    allocator.observe_order_id(
        250
    )

    assert allocator.initialized is False

    with pytest.raises(
        RuntimeError,
        match="not initialized",
    ):
        allocator.allocate()


def test_initialization_respects_previously_observed_id() -> None:
    """nextValidId must remain above previously observed IB IDs."""

    allocator = IBOrderIdAllocator()

    allocator.observe_order_id(
        250
    )

    allocator.initialize(
        100
    )

    assert allocator.next_order_id == 251


def test_higher_observed_id_advances_initialized_allocator() -> None:
    """Observed higher order ID should advance allocation floor."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.observe_order_id(
        150
    )

    assert allocator.next_order_id == 151


def test_lower_observed_id_does_not_regress_allocator() -> None:
    """Observed stale IDs must not move sequence backward."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.observe_order_id(
        150
    )

    allocator.observe_order_id(
        120
    )

    assert allocator.next_order_id == 151

    assert (
        allocator.highest_observed_order_id
        == 150
    )


def test_observing_current_next_id_advances_allocator() -> None:
    """Observed collision with next ID should move allocator above it."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.observe_order_id(
        100
    )

    assert allocator.next_order_id == 101


def test_observed_allocated_id_does_not_create_gap_unnecessarily() -> None:
    """Seeing an already allocated ID should not advance again."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    assert allocator.allocate() == 100
    assert allocator.next_order_id == 101

    allocator.observe_order_id(
        100
    )

    assert allocator.next_order_id == 101


def test_ensure_minimum_advances_initialized_allocator() -> None:
    """Durable execution floor may advance next allocation."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.ensure_minimum_next_id(
        500
    )

    assert allocator.next_order_id == 500


def test_ensure_minimum_does_not_regress_allocator() -> None:
    """Durable minimum may never move sequence backward."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        500
    )

    allocator.ensure_minimum_next_id(
        100
    )

    assert allocator.next_order_id == 500


def test_ensure_minimum_does_not_initialize_allocator() -> None:
    """Durable floor alone must not replace nextValidId."""

    allocator = IBOrderIdAllocator()

    allocator.ensure_minimum_next_id(
        500
    )

    assert allocator.initialized is False
    assert allocator.next_order_id is None


def test_snapshot_reports_uninitialized_state() -> None:
    """Snapshot should represent new allocator correctly."""

    allocator = IBOrderIdAllocator()

    result = allocator.snapshot()

    assert result == IBOrderIdSnapshot(
        initialized=False,
        next_order_id=None,
        highest_allocated_order_id=None,
        highest_observed_order_id=None,
    )


def test_snapshot_reports_current_state() -> None:
    """Snapshot should capture allocation and observations."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        100
    )

    allocator.allocate()

    allocator.observe_order_id(
        150
    )

    result = allocator.snapshot()

    assert result == IBOrderIdSnapshot(
        initialized=True,
        next_order_id=151,
        highest_allocated_order_id=100,
        highest_observed_order_id=150,
    )


def test_snapshot_is_immutable() -> None:
    """Allocator snapshots must not be mutable."""

    allocator = IBOrderIdAllocator()

    result = allocator.snapshot()

    with pytest.raises(
        AttributeError,
    ):
        result.initialized = True  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_id",
    [
        -1,
        True,
        1.5,
        "100",
        None,
    ],
)
def test_invalid_next_valid_id_is_rejected(
    invalid_id,
) -> None:
    """nextValidId value must be a valid IB order ID."""

    allocator = IBOrderIdAllocator()

    with pytest.raises(
        ValueError,
        match="'next_valid_id'",
    ):
        allocator.initialize(
            invalid_id
        )


@pytest.mark.parametrize(
    "invalid_id",
    [
        -1,
        True,
        1.5,
        "100",
        None,
    ],
)
def test_invalid_observed_order_id_is_rejected(
    invalid_id,
) -> None:
    """Observed IB order ID must be valid."""

    allocator = IBOrderIdAllocator()

    with pytest.raises(
        ValueError,
        match="'order_id'",
    ):
        allocator.observe_order_id(
            invalid_id
        )


@pytest.mark.parametrize(
    "invalid_id",
    [
        -1,
        True,
        1.5,
        "100",
        None,
    ],
)
def test_invalid_minimum_next_id_is_rejected(
    invalid_id,
) -> None:
    """Durable minimum next ID must be valid."""

    allocator = IBOrderIdAllocator()

    with pytest.raises(
        ValueError,
        match="'minimum_next_id'",
    ):
        allocator.ensure_minimum_next_id(
            invalid_id
        )


def test_concurrent_allocations_are_unique() -> None:
    """Concurrent BTS activity must never receive duplicate IDs."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        1000
    )

    allocation_count = 500

    with ThreadPoolExecutor(
        max_workers=20
    ) as executor:
        results = list(
            executor.map(
                lambda _: allocator.allocate(),
                range(
                    allocation_count
                ),
            )
        )

    assert len(results) == allocation_count

    assert (
        len(
            set(
                results
            )
        )
        == allocation_count
    )


def test_concurrent_allocations_cover_contiguous_sequence() -> None:
    """Thread-safe allocation should not lose or skip IDs."""

    allocator = IBOrderIdAllocator()

    allocator.initialize(
        1000
    )

    allocation_count = 500

    with ThreadPoolExecutor(
        max_workers=20
    ) as executor:
        results = list(
            executor.map(
                lambda _: allocator.allocate(),
                range(
                    allocation_count
                ),
            )
        )

    assert sorted(
        results
    ) == list(
        range(
            1000,
            1500,
        )
    )

    assert allocator.next_order_id == 1500