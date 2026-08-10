"""WebSocket client for receiving messages from Eagle."""

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent


EagleMessage = (
    EagleHello
    | EagleHeartbeat
    | IncomingLifecycleEvent
)


class EagleAuthenticationError(ConnectionError):
    """Raised when Eagle rejects the WebSocket handshake with HTTP 401."""


class EagleRateLimitError(ConnectionError):
    """Raised when Eagle rejects the WebSocket handshake with HTTP 429."""

    def __init__(
        self,
        message: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class EagleClient:
    """Connect to Eagle and convert JSON frames into validated messages."""

    def __init__(
        self,
        uri: str,
        api_key: str | None = None,
        since_seq: int | None = None,
    ) -> None:
        """Create an Eagle WebSocket client.

        Args:
            uri:
                WebSocket address of the Eagle server, such as
                ``ws://localhost:8765`` or an Eagle ``wss://`` address.

            api_key:
                Optional Eagle API key used for authentication.

                When supplied, BTS sends the key in the ``x-api-key``
                HTTP header during the WebSocket opening handshake.

            since_seq:
                Optional durable Eagle sequence cursor.

                When supplied, BTS adds ``since_seq`` to the WebSocket
                connection URL so Eagle can replay events newer than the
                durable BTS cursor.
        """

        if not isinstance(uri, str) or not uri.strip():
            raise ValueError("'uri' must be a non-empty string.")

        if api_key is not None:
            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError(
                    "'api_key' must be a non-empty string when supplied."
                )

            api_key = api_key.strip()

        if since_seq is not None:
            if (
                not isinstance(since_seq, int)
                or isinstance(since_seq, bool)
                or since_seq < 0
            ):
                raise ValueError(
                    "'since_seq' must be a non-negative integer "
                    "when supplied."
                )

        self._uri = uri.strip()
        self._api_key = api_key
        self._since_seq = since_seq

    @property
    def uri(self) -> str:
        """Return the configured base Eagle WebSocket address."""

        return self._uri

    @property
    def since_seq(self) -> int | None:
        """Return the configured Eagle replay cursor."""

        return self._since_seq

    @property
    def has_api_key(self) -> bool:
        """Return True when an Eagle API key is configured."""

        return self._api_key is not None

    def _connection_uri(self) -> str:
        """Return the WebSocket URI including the optional replay cursor."""

        if self._since_seq is None:
            return self._uri

        parsed_uri = urlsplit(
            self._uri
        )

        query_items = parse_qsl(
            parsed_uri.query,
            keep_blank_values=True,
        )

        query_items = [
            (key, value)
            for key, value in query_items
            if key != "since_seq"
        ]

        query_items.append(
            (
                "since_seq",
                str(self._since_seq),
            )
        )

        updated_query = urlencode(
            query_items
        )

        return urlunsplit(
            (
                parsed_uri.scheme,
                parsed_uri.netloc,
                parsed_uri.path,
                updated_query,
                parsed_uri.fragment,
            )
        )

    def _connection_headers(self) -> dict[str, str] | None:
        """Create authentication headers for the WebSocket handshake."""

        if self._api_key is None:
            return None

        return {
            "x-api-key": self._api_key,
        }

    @staticmethod
    def _parse_retry_after(value: str | None) -> int | None:
        """Parse an integer Retry-After value from an HTTP header."""

        if value is None:
            return None

        try:
            retry_after = int(value)

        except ValueError:
            return None

        if retry_after < 0:
            return None

        return retry_after

    @classmethod
    def _raise_for_handshake_error(
        cls,
        error: InvalidStatus,
    ) -> None:
        """Translate Eagle HTTP handshake failures into BTS exceptions."""

        status_code = error.response.status_code

        if status_code == 401:
            raise EagleAuthenticationError(
                "Eagle rejected the WebSocket connection because "
                "authentication failed."
            ) from error

        if status_code == 429:
            retry_after_value = error.response.headers.get(
                "Retry-After"
            )

            retry_after_seconds = cls._parse_retry_after(
                retry_after_value
            )

            raise EagleRateLimitError(
                "Eagle rejected the WebSocket connection because "
                "the client is rate limited or has too many active "
                "connections.",
                retry_after_seconds=retry_after_seconds,
            ) from error

        raise ConnectionError(
            f"Eagle WebSocket handshake failed with HTTP "
            f"{status_code}."
        ) from error

    @staticmethod
    def _parse_message(
        raw_message: str,
    ) -> EagleMessage:
        """Parse one raw JSON frame received from Eagle.

        fund.hello frames become EagleHello objects.

        fund.heartbeat frames become EagleHeartbeat objects.

        Lifecycle frames become IncomingLifecycleEvent objects.
        """

        if not isinstance(raw_message, str):
            raise TypeError(
                "'raw_message' must be a string."
            )

        try:
            decoded_message: Any = json.loads(
                raw_message
            )

        except json.JSONDecodeError as error:
            raise ValueError(
                "Eagle message must contain valid JSON."
            ) from error

        if not isinstance(decoded_message, dict):
            raise ValueError(
                "Eagle message must decode to a JSON object."
            )

        message_type = decoded_message.get(
            "type"
        )

        if message_type == "fund.hello":
            return EagleHello.from_dict(
                decoded_message
            )

        if message_type == "fund.heartbeat":
            return EagleHeartbeat.from_dict(
                decoded_message
            )

        return IncomingLifecycleEvent.from_dict(
            decoded_message
        )

    async def receive_one(self) -> EagleMessage:
        """Connect to Eagle and receive one validated message."""

        try:
            async with connect(
                self._connection_uri(),
                additional_headers=self._connection_headers(),
                open_timeout=10,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=1_048_576,
            ) as websocket:
                raw_message = await websocket.recv()

        except InvalidStatus as error:
            self._raise_for_handshake_error(
                error
            )

        except ConnectionClosed as error:
            raise ConnectionError(
                "The Eagle WebSocket connection closed before a complete "
                "message was received."
            ) from error

        if not isinstance(raw_message, str):
            raise ValueError(
                "Eagle must send JSON as a WebSocket text message."
            )

        return self._parse_message(
            raw_message
        )

    async def listen(
        self,
    ) -> AsyncIterator[EagleMessage]:
        """Continuously receive validated messages from Eagle."""

        try:
            async with connect(
                self._connection_uri(),
                additional_headers=self._connection_headers(),
                open_timeout=10,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=1_048_576,
            ) as websocket:
                async for raw_message in websocket:
                    if not isinstance(
                        raw_message,
                        str,
                    ):
                        raise ValueError(
                            "Eagle must send JSON as a "
                            "WebSocket text message."
                        )

                    yield self._parse_message(
                        raw_message
                    )

        except InvalidStatus as error:
            self._raise_for_handshake_error(
                error
            )

        except ConnectionClosed as error:
            raise ConnectionError(
                "The Eagle WebSocket connection closed unexpectedly."
            ) from error