"""WebSocket client for receiving lifecycle events from Eagle."""

import json
from collections.abc import AsyncIterator
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.communications.incoming_event import IncomingLifecycleEvent




class EagleClient:
    """Connect to Eagle and convert JSON messages into lifecycle events."""

    def __init__(self, uri: str) -> None:
        """Create an Eagle WebSocket client.

        Args:
            uri:
                WebSocket address of the Eagle server, such as
                ``ws://localhost:8765``.
        """

        if not isinstance(uri, str) or not uri.strip():
            raise ValueError("'uri' must be a non-empty string.")

        self._uri = uri.strip()

    @property
    def uri(self) -> str:
        """Return the configured Eagle WebSocket address."""

        return self._uri

    @staticmethod
    def _parse_message(raw_message: str) -> IncomingLifecycleEvent:
        """Parse one raw JSON message received from Eagle.

        Args:
            raw_message:
                JSON text received from the WebSocket.

        Returns:
            A validated IncomingLifecycleEvent.

        Raises:
            TypeError:
                If raw_message isn't a string.

            ValueError:
                If the message isn't valid JSON, isn't a JSON object,
                or doesn't satisfy the lifecycle-event blueprint.
        """

        if not isinstance(raw_message, str):
            raise TypeError("'raw_message' must be a string.")

        try:
            decoded_message: Any = json.loads(raw_message)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Eagle message must contain valid JSON."
            ) from error

        if not isinstance(decoded_message, dict):
            raise ValueError(
                "Eagle message must decode to a JSON object."
            )

        return IncomingLifecycleEvent.from_dict(decoded_message)

    async def receive_one(self) -> IncomingLifecycleEvent:
        """Connect to Eagle and receive one lifecycle event.

        This method is intentionally limited to one message for the first
        WebSocket integration milestone. Later, a continuous receive loop,
        reconnect logic, authentication, and heartbeat handling will be added.
        """

        try:
            async with connect(
                self._uri,
                open_timeout=10,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=1_048_576,
            ) as websocket:
                raw_message = await websocket.recv()

        except ConnectionClosed as error:
            raise ConnectionError(
                "The Eagle WebSocket connection closed before a complete "
                "message was received."
            ) from error

        if not isinstance(raw_message, str):
            raise ValueError(
                "Eagle must send JSON as a WebSocket text message."
            )

        return self._parse_message(raw_message)
    
    async def listen(self) -> AsyncIterator[IncomingLifecycleEvent]:
        """Continuously receive validated lifecycle events from Eagle.

        The client maintains one WebSocket connection and yields each valid
        lifecycle event as it arrives.

        Yields:
            Validated IncomingLifecycleEvent objects.

        Raises:
            ConnectionError:
                If the Eagle WebSocket connection closes unexpectedly.

            ValueError:
                If Eagle sends a binary message or an invalid lifecycle event.
        """

        try:
            async with connect(
                self._uri,
                open_timeout=10,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=1_048_576,
            ) as websocket:
                async for raw_message in websocket:
                    if not isinstance(raw_message, str):
                        raise ValueError(
                            "Eagle must send JSON as a WebSocket text message."
                        )

                    yield self._parse_message(raw_message)

        except ConnectionClosed as error:
            raise ConnectionError(
                "The Eagle WebSocket connection closed unexpectedly."
            ) from error
    