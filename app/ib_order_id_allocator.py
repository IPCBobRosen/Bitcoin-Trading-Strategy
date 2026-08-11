"""Thread-safe Interactive Brokers order ID allocation for BTS."""

from dataclasses import dataclass
import threading
from typing import Any


@dataclass(frozen=True, slots=True)
class IBOrderIdSnapshot:
    """Immutable snapshot of IB order-ID allocator state."""

    initialized: bool
    next_order_id: int | None
    highest_allocated_order_id: int | None
    highest_observed_order_id: int | None


class IBOrderIdAllocator:
    """Allocate unique monotonically increasing IB API order IDs.

    The allocator must receive at least one nextValidId value before
    BTS may allocate an order ID.

    Order IDs observed later through IB callbacks may advance the
    allocator floor. The allocator never moves backward.

    This object is thread-safe.
    """

    def __init__(self) -> None:
        """Create an uninitialized IB order-ID allocator."""

        self._initialized = False

        self._next_order_id: int | None = None

        self._highest_allocated_order_id: int | None = None

        self._highest_observed_order_id: int | None = None

        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        """Return True after nextValidId has initialized the allocator."""

        with self._lock:
            return self._initialized

    @property
    def next_order_id(self) -> int | None:
        """Return the next ID that would currently be allocated."""

        with self._lock:
            return self._next_order_id

    @property
    def highest_allocated_order_id(
        self,
    ) -> int | None:
        """Return the highest order ID allocated by BTS."""

        with self._lock:
            return self._highest_allocated_order_id

    @property
    def highest_observed_order_id(
        self,
    ) -> int | None:
        """Return the highest order ID observed from IB."""

        with self._lock:
            return self._highest_observed_order_id

    def initialize(
        self,
        next_valid_id: int,
    ) -> None:
        """Initialize or advance the allocator from IB nextValidId.

        Repeated initialization is safe.

        A later nextValidId may advance the allocator, but it may
        never cause BTS to reuse or move backward to an older ID.
        """

        validated_id = (
            self._validate_order_id(
                next_valid_id,
                "next_valid_id",
            )
        )

        with self._lock:
            candidate = validated_id

            if (
                self._highest_observed_order_id
                is not None
            ):
                candidate = max(
                    candidate,
                    self._highest_observed_order_id + 1,
                )

            if (
                self._next_order_id
                is not None
            ):
                candidate = max(
                    candidate,
                    self._next_order_id,
                )

            if (
                self._highest_allocated_order_id
                is not None
            ):
                candidate = max(
                    candidate,
                    self._highest_allocated_order_id + 1,
                )

            self._next_order_id = candidate
            self._initialized = True

    def allocate(self) -> int:
        """Return one unique order ID and advance the sequence.

        Raises:
            RuntimeError:
                If IB nextValidId has not initialized the allocator.
        """

        with self._lock:
            if (
                not self._initialized
                or self._next_order_id is None
            ):
                raise RuntimeError(
                    "IB order-ID allocator is not initialized."
                )

            allocated_id = self._next_order_id

            self._highest_allocated_order_id = (
                allocated_id
            )

            self._next_order_id = (
                allocated_id + 1
            )

            return allocated_id

    def observe_order_id(
        self,
        order_id: int,
    ) -> None:
        """Observe an IB order ID and advance the allocation floor.

        This is intended for IDs received through callbacks such as
        openOrder or orderStatus.

        Observing an ID does not by itself initialize the allocator.
        BTS still requires nextValidId before allocating orders.
        """

        validated_id = (
            self._validate_order_id(
                order_id,
                "order_id",
            )
        )

        with self._lock:
            if (
                self._highest_observed_order_id
                is None
                or validated_id
                > self._highest_observed_order_id
            ):
                self._highest_observed_order_id = (
                    validated_id
                )

            if (
                self._initialized
                and self._next_order_id is not None
                and validated_id
                >= self._next_order_id
            ):
                self._next_order_id = (
                    validated_id + 1
                )

    def ensure_minimum_next_id(
        self,
        minimum_next_id: int,
    ) -> None:
        """Advance the allocator to at least a supplied next ID.

        This method is useful when BTS restores durable execution
        records containing broker order IDs and wants to guarantee
        that a future allocation remains above them.

        It does not initialize the allocator.
        """

        validated_id = (
            self._validate_order_id(
                minimum_next_id,
                "minimum_next_id",
            )
        )

        with self._lock:
            if (
                self._next_order_id is None
            ):
                return

            if (
                validated_id
                > self._next_order_id
            ):
                self._next_order_id = (
                    validated_id
                )

    def snapshot(
        self,
    ) -> IBOrderIdSnapshot:
        """Return an immutable allocator snapshot."""

        with self._lock:
            return IBOrderIdSnapshot(
                initialized=self._initialized,
                next_order_id=self._next_order_id,
                highest_allocated_order_id=(
                    self._highest_allocated_order_id
                ),
                highest_observed_order_id=(
                    self._highest_observed_order_id
                ),
            )

    @staticmethod
    def _validate_order_id(
        value: Any,
        field_name: str,
    ) -> int:
        """Validate an IB API order identifier."""

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(
                f"'{field_name}' must be a "
                "non-negative integer."
            )

        return value