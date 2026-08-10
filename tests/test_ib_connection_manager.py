"""Tests for the Interactive Brokers connection manager."""

from unittest.mock import MagicMock, patch

import pytest

from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager


def create_app() -> IBApiPositionApp:
    """Create an IB API app for connection-manager tests."""

    return IBApiPositionApp(
        IBBrokerClient()
    )


def test_manager_retains_configuration() -> None:
    """Connection manager should retain its IB settings."""

    app = create_app()

    manager = IBConnectionManager(
        app,
        host="127.0.0.1",
        port=7497,
        client_id=7,
    )

    assert manager.app is app
    assert manager.host == "127.0.0.1"
    assert manager.port == 7497
    assert manager.client_id == 7


def test_new_manager_is_not_ready() -> None:
    """Disconnected manager must not report API readiness."""

    app = create_app()

    manager = IBConnectionManager(
        app
    )

    with patch.object(
        app,
        "isConnected",
        return_value=False,
    ):
        assert manager.ready is False


def test_socket_connection_without_handshake_is_not_ready() -> None:
    """Socket connectivity alone must not satisfy readiness."""

    app = create_app()

    manager = IBConnectionManager(
        app
    )

    with patch.object(
        app,
        "isConnected",
        return_value=True,
    ):
        assert manager.ready is False


def test_socket_and_next_valid_id_are_ready() -> None:
    """Socket plus nextValidId should satisfy readiness."""

    app = create_app()

    app.nextValidId(
        100
    )

    manager = IBConnectionManager(
        app
    )

    with patch.object(
        app,
        "isConnected",
        return_value=True,
    ):
        assert manager.ready is True


def test_invalid_app_is_rejected() -> None:
    """Connection manager requires an IBApiPositionApp."""

    with pytest.raises(
        TypeError,
        match="'app' must be an IBApiPositionApp",
    ):
        IBConnectionManager(
            object()  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "host",
    [
        "",
        "   ",
    ],
)
def test_invalid_host_is_rejected(
    host: str,
) -> None:
    """Host must contain a usable value."""

    with pytest.raises(
        ValueError,
        match="'host' must be a non-empty string",
    ):
        IBConnectionManager(
            create_app(),
            host=host,
        )


@pytest.mark.parametrize(
    "port",
    [
        0,
        -1,
        65536,
        True,
    ],
)
def test_invalid_port_is_rejected(
    port: object,
) -> None:
    """IB API port must be a valid TCP port."""

    with pytest.raises(
        ValueError,
        match="'port' must be an integer",
    ):
        IBConnectionManager(
            create_app(),
            port=port,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "client_id",
    [
        -1,
        True,
    ],
)
def test_invalid_client_id_is_rejected(
    client_id: object,
) -> None:
    """IB client ID must be a non-negative integer."""

    with pytest.raises(
        ValueError,
        match="'client_id' must be a non-negative integer",
    ):
        IBConnectionManager(
            create_app(),
            client_id=client_id,  # type: ignore[arg-type]
        )


def test_connect_calls_official_ib_connect() -> None:
    """Manager should connect and start the IB network loop."""

    app = create_app()

    manager = IBConnectionManager(
        app,
        host="127.0.0.1",
        port=7497,
        client_id=12,
    )

    with (
        patch.object(
            app,
            "isConnected",
            return_value=False,
        ),
        patch.object(
            app,
            "connect",
        ) as connect,
        patch.object(
            app,
            "run",
        ) as run,
        patch.object(
            manager,
            "_wait_until_ready",
        ) as wait_until_ready,
        patch(
            "app.ib_connection_manager.threading.Thread"
        ) as thread_class,
    ):
        thread = MagicMock()
        thread_class.return_value = thread

        manager.connect()

        connect.assert_called_once_with(
            "127.0.0.1",
            7497,
            clientId=12,
        )

        thread_class.assert_called_once_with(
            target=run,
            name="ib-api-network-loop",
            daemon=True,
        )

        thread.start.assert_called_once_with()

        wait_until_ready.assert_called_once_with()


def test_connect_resets_stale_handshake_state() -> None:
    """New connections must not reuse readiness from an old session."""

    app = create_app()

    app.nextValidId(
        100
    )

    assert app.api_ready.ready is True

    manager = IBConnectionManager(
        app
    )

    with (
        patch.object(
            app,
            "isConnected",
            return_value=False,
        ),
        patch.object(
            app,
            "connect",
        ),
        patch.object(
            app,
            "run",
        ),
        patch.object(
            manager,
            "_wait_until_ready",
        ),
        patch(
            "app.ib_connection_manager.threading.Thread"
        ) as thread_class,
    ):
        thread = MagicMock()
        thread_class.return_value = thread

        manager.connect()

    assert app.api_ready.ready is False


def test_connect_rejects_already_connected_app() -> None:
    """Duplicate connection attempts should be rejected."""

    app = create_app()

    manager = IBConnectionManager(
        app
    )

    with patch.object(
        app,
        "isConnected",
        return_value=True,
    ):
        with pytest.raises(
            RuntimeError,
            match="already connected",
        ):
            manager.connect()


def test_request_snapshot_requires_socket_connection() -> None:
    """Position requests must not run while disconnected."""

    app = create_app()

    manager = IBConnectionManager(
        app
    )

    with patch.object(
        app,
        "isConnected",
        return_value=False,
    ):
        with pytest.raises(
            RuntimeError,
            match="not connected",
        ):
            manager.request_position_snapshot()


def test_request_snapshot_requires_handshake_ready() -> None:
    """Connected socket without nextValidId must block requests."""

    app = create_app()

    manager = IBConnectionManager(
        app
    )

    with patch.object(
        app,
        "isConnected",
        return_value=True,
    ):
        with pytest.raises(
            RuntimeError,
            match="handshake is not ready",
        ):
            manager.request_position_snapshot()


def test_ready_snapshot_delegates_to_app() -> None:
    """Fully ready manager should delegate the position request."""

    app = create_app()

    app.nextValidId(
        100
    )

    manager = IBConnectionManager(
        app
    )

    with (
        patch.object(
            app,
            "isConnected",
            return_value=True,
        ),
        patch.object(
            app,
            "request_position_snapshot",
        ) as request_snapshot,
    ):
        manager.request_position_snapshot()

    request_snapshot.assert_called_once_with()


def test_wait_until_ready_returns_after_handshake() -> None:
    """Ready socket and handshake should complete immediately."""

    app = create_app()

    app.nextValidId(
        100
    )

    manager = IBConnectionManager(
        app,
        connection_timeout_seconds=1.0,
    )

    with patch.object(
        app,
        "isConnected",
        return_value=True,
    ):
        manager._wait_until_ready()


def test_wait_until_ready_times_out_without_handshake() -> None:
    """Missing nextValidId should cause a safe timeout."""

    app = create_app()

    manager = IBConnectionManager(
        app,
        connection_timeout_seconds=0.001,
        poll_interval_seconds=0.0001,
    )

    with (
        patch.object(
            app,
            "isConnected",
            return_value=True,
        ),
        patch.object(
            app,
            "disconnect",
        ) as disconnect,
    ):
        with pytest.raises(
            TimeoutError,
            match="connection handshake",
        ):
            manager._wait_until_ready()

    disconnect.assert_called_once_with()
    assert app.api_ready.ready is False


def test_disconnect_calls_ib_disconnect() -> None:
    """Connected IB application should be disconnected cleanly."""

    app = create_app()

    app.nextValidId(
        100
    )

    manager = IBConnectionManager(
        app
    )

    with (
        patch.object(
            app,
            "isConnected",
            return_value=True,
        ),
        patch.object(
            app,
            "disconnect",
        ) as disconnect,
    ):
        manager.disconnect()

    disconnect.assert_called_once_with()
    assert manager.network_thread is None
    assert app.api_ready.ready is False


def test_disconnect_when_not_connected_is_safe() -> None:
    """Disconnect should be harmless when no IB connection exists."""

    app = create_app()

    manager = IBConnectionManager(
        app
    )

    with (
        patch.object(
            app,
            "isConnected",
            return_value=False,
        ),
        patch.object(
            app,
            "disconnect",
        ) as disconnect,
    ):
        manager.disconnect()

    disconnect.assert_not_called()
    assert manager.network_thread is None
    assert app.api_ready.ready is False