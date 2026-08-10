"""Manage the Interactive Brokers API connection lifecycle."""

import threading
import time
from collections.abc import Callable

from app.ib_api_position_app import IBApiPositionApp


class IBConnectionManager:
    """Start and stop an IBKR API application safely."""

    def __init__(
        self,
        app: IBApiPositionApp,
        *,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        connection_timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.05,
        sleep_function: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create an IBKR connection manager."""

        if not isinstance(
            app,
            IBApiPositionApp,
        ):
            raise TypeError(
                "'app' must be an IBApiPositionApp."
            )

        if (
            not isinstance(host, str)
            or not host.strip()
        ):
            raise ValueError(
                "'host' must be a non-empty string."
            )

        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ValueError(
                "'port' must be an integer from 1 through 65535."
            )

        if (
            isinstance(client_id, bool)
            or not isinstance(client_id, int)
            or client_id < 0
        ):
            raise ValueError(
                "'client_id' must be a non-negative integer."
            )

        if (
            isinstance(
                connection_timeout_seconds,
                bool,
            )
            or not isinstance(
                connection_timeout_seconds,
                (int, float),
            )
            or connection_timeout_seconds <= 0
        ):
            raise ValueError(
                "'connection_timeout_seconds' must be positive."
            )

        if (
            isinstance(
                poll_interval_seconds,
                bool,
            )
            or not isinstance(
                poll_interval_seconds,
                (int, float),
            )
            or poll_interval_seconds <= 0
        ):
            raise ValueError(
                "'poll_interval_seconds' must be positive."
            )

        if not callable(
            sleep_function
        ):
            raise TypeError(
                "'sleep_function' must be callable."
            )

        self._app = app
        self._host = host.strip()
        self._port = port
        self._client_id = client_id

        self._connection_timeout_seconds = float(
            connection_timeout_seconds
        )

        self._poll_interval_seconds = float(
            poll_interval_seconds
        )

        self._sleep_function = sleep_function

        self._network_thread: threading.Thread | None = None

    @property
    def app(self) -> IBApiPositionApp:
        """Return the managed IB API application."""

        return self._app

    @property
    def host(self) -> str:
        """Return the configured IBKR host."""

        return self._host

    @property
    def port(self) -> int:
        """Return the configured IBKR API port."""

        return self._port

    @property
    def client_id(self) -> int:
        """Return the configured IBKR API client ID."""

        return self._client_id

    @property
    def network_thread(
        self,
    ) -> threading.Thread | None:
        """Return the IB API network thread when created."""

        return self._network_thread

    @property
    def ready(self) -> bool:
        """Return True when socket and IBKR handshake are ready."""

        return (
            self._app.isConnected()
            and self._app.api_ready.ready
        )

    def connect(self) -> None:
        """Connect to TWS or IB Gateway and wait for API readiness."""

        if self._app.isConnected():
            raise RuntimeError(
                "IB API application is already connected."
            )

        # Prevent stale nextValidId state from a previous session
        # from satisfying readiness for a new connection.
        self._app.api_ready.reset()

        self._app.connect(
            self._host,
            self._port,
            clientId=self._client_id,
        )

        self._network_thread = threading.Thread(
            target=self._app.run,
            name="ib-api-network-loop",
            daemon=True,
        )

        self._network_thread.start()

        self._wait_until_ready()

    def disconnect(self) -> None:
        """Disconnect from IBKR and clear local connection state."""

        if self._app.isConnected():
            self._app.disconnect()

        self._app.api_ready.reset()

        network_thread = self._network_thread

        if (
            network_thread is not None
            and network_thread.is_alive()
            and network_thread is not threading.current_thread()
        ):
            network_thread.join(
                timeout=self._connection_timeout_seconds
            )

        self._network_thread = None

    def request_position_snapshot(self) -> None:
        """Request positions only after full IB API readiness."""

        if not self._app.isConnected():
            raise RuntimeError(
                "IB API application is not connected."
            )

        if not self._app.api_ready.ready:
            raise RuntimeError(
                "IB API handshake is not ready."
            )

        self._app.request_position_snapshot()

    def _wait_until_ready(self) -> None:
        """Wait for socket connection and nextValidId handshake."""

        deadline = (
            time.monotonic()
            + self._connection_timeout_seconds
        )

        while not self.ready:
            if time.monotonic() >= deadline:
                self._cleanup_failed_connection()

                raise TimeoutError(
                    "Timed out waiting for the IB API "
                    "connection handshake."
                )

            self._sleep_function(
                self._poll_interval_seconds
            )

    def _cleanup_failed_connection(self) -> None:
        """Clean up after an unsuccessful connection attempt."""

        if self._app.isConnected():
            self._app.disconnect()

        self._app.api_ready.reset()

        self._network_thread = None