import json
from typing import Any

from app.communications.incoming_event import IncomingLifecycleEvent


class EagleClient:
    """
    Handles communication-related processing for messages received from Eagle.

    The real network connection will be added later. For now, this class
    converts incoming JSON text into validated IncomingLifecycleEvent objects.
    """

    def _parse_message(self, raw_message: str) -> IncomingLifecycleEvent:
        """
        Convert a JSON string into a validated IncomingLifecycleEvent.

        Args:
            raw_message: Raw JSON text received from Eagle.

        Returns:
            A validated IncomingLifecycleEvent object.

        Raises:
            TypeError: If raw_message is not a string.
            ValueError: If the message is invalid JSON or is not a JSON object.
        """

        if not isinstance(raw_message, str):
            raise TypeError("raw_message must be a string")

        try:
            decoded_message: Any = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise ValueError("raw_message is not valid JSON") from exc

        if not isinstance(decoded_message, dict):
            raise ValueError("raw_message must contain a JSON object")

        return IncomingLifecycleEvent.from_dict(decoded_message)