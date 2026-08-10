"""Track Eagle replay progress after fund.hello."""

from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent


class ReplayTracker:
    """Track whether Eagle reconnect replay has been fully drained."""

    def __init__(self) -> None:
        """Create an empty replay tracker."""

        self._hello_received = False
        self._expected_replay_count = 0
        self._processed_replay_count = 0
        self._server_last_seq: int | None = None
        self._requested_since_seq: int | None = None

    @property
    def hello_received(self) -> bool:
        """Return True after fund.hello has been processed."""

        return self._hello_received

    @property
    def expected_replay_count(self) -> int:
        """Return the number of lifecycle replay events Eagle announced."""

        return self._expected_replay_count

    @property
    def processed_replay_count(self) -> int:
        """Return the number of replay lifecycle events processed so far."""

        return self._processed_replay_count

    @property
    def server_last_seq(self) -> int | None:
        """Return Eagle's last_seq value from fund.hello."""

        return self._server_last_seq

    @property
    def requested_since_seq(self) -> int | None:
        """Return the since_seq value echoed by Eagle in fund.hello."""

        return self._requested_since_seq

    @property
    def replay_complete(self) -> bool:
        """Return True when the announced replay has been fully processed."""

        return (
            self._hello_received
            and self._processed_replay_count
            >= self._expected_replay_count
        )

    def process_hello(
        self,
        hello: EagleHello,
    ) -> None:
        """Start replay tracking from a validated fund.hello frame."""

        if not isinstance(hello, EagleHello):
            raise TypeError(
                "'hello' must be an EagleHello."
            )

        self._hello_received = True
        self._expected_replay_count = hello.replay_count
        self._processed_replay_count = 0
        self._server_last_seq = hello.last_seq
        self._requested_since_seq = hello.since_seq

    def record_lifecycle_event(
        self,
        event: IncomingLifecycleEvent,
    ) -> None:
        """Record one lifecycle event received during replay."""

        if not isinstance(event, IncomingLifecycleEvent):
            raise TypeError(
                "'event' must be an IncomingLifecycleEvent."
            )

        if not self._hello_received:
            raise RuntimeError(
                "Cannot record replay events before fund.hello."
            )

        if self.replay_complete:
            return

        self._processed_replay_count += 1