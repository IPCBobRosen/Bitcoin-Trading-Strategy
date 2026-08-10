"""Translate Interactive Brokers position callbacks into BTS records."""

from decimal import Decimal
from typing import Any

from app.ib_broker_client import (
    IBBrokerClient,
    IBPositionRecord,
)


class IBPositionTransport:
    """Bridge IBKR position callbacks into IBBrokerClient.

    This class deliberately does not open a network connection.

    A future real IBKR EWrapper implementation can delegate its
    position(...) and positionEnd() callbacks to this transport.
    """

    def __init__(
        self,
        broker_client: IBBrokerClient,
    ) -> None:
        """Create an IB position callback transport."""

        if not isinstance(
            broker_client,
            IBBrokerClient,
        ):
            raise TypeError(
                "'broker_client' must be an IBBrokerClient."
            )

        self._broker_client = broker_client

    @property
    def broker_client(self) -> IBBrokerClient:
        """Return the underlying IB broker client."""

        return self._broker_client

    def begin_snapshot(self) -> None:
        """Prepare BTS to receive an IB reqPositions snapshot."""

        self._broker_client.begin_position_snapshot()

    def position(
        self,
        account: str,
        contract: Any,
        position: Decimal | int | float | str,
        average_cost: float,
    ) -> None:
        """Handle one IBKR position callback.

        Args:
            account:
                IBKR account holding the position.

            contract:
                IBKR Contract-like object supplied by the callback.

            position:
                Signed position quantity.

            average_cost:
                Average position cost reported by IBKR.
        """

        if not isinstance(
            account,
            str,
        ) or not account.strip():
            raise ValueError(
                "'account' must be a non-empty string."
            )

        if contract is None:
            raise TypeError(
                "'contract' must be supplied."
            )

        if isinstance(
            average_cost,
            bool,
        ) or not isinstance(
            average_cost,
            (int, float),
        ):
            raise ValueError(
                "'average_cost' must be numeric."
            )

        record = IBPositionRecord(
            account=account.strip(),
            symbol=self._read_string(
                contract,
                "symbol",
            ),
            position=position,
            con_id=self._read_optional_int(
                contract,
                "conId",
            ),
            local_symbol=self._read_optional_string(
                contract,
                "localSymbol",
            ),
            trading_class=self._read_optional_string(
                contract,
                "tradingClass",
            ),
            last_trade_date=self._read_optional_string(
                contract,
                "lastTradeDateOrContractMonth",
            ),
            average_cost=float(
                average_cost
            ),
        )

        self._broker_client.receive_position(
            record
        )

    def position_end(self) -> None:
        """Handle the IBKR positionEnd callback."""

        self._broker_client.finish_position_snapshot()

    @staticmethod
    def _read_string(
        contract: Any,
        field_name: str,
    ) -> str:
        """Read a required string field from an IB Contract-like object."""

        value = getattr(
            contract,
            field_name,
            None,
        )

        if not isinstance(
            value,
            str,
        ):
            return ""

        return value.strip()

    @staticmethod
    def _read_optional_string(
        contract: Any,
        field_name: str,
    ) -> str | None:
        """Read an optional string field from an IB Contract-like object."""

        value = getattr(
            contract,
            field_name,
            None,
        )

        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                f"IB contract '{field_name}' must be a string."
            )

        normalized = value.strip()

        if not normalized:
            return None

        return normalized

    @staticmethod
    def _read_optional_int(
        contract: Any,
        field_name: str,
    ) -> int | None:
        """Read an optional integer field from an IB Contract-like object."""

        value = getattr(
            contract,
            field_name,
            None,
        )

        if value is None:
            return None

        if isinstance(
            value,
            bool,
        ) or not isinstance(
            value,
            int,
        ):
            raise ValueError(
                f"IB contract '{field_name}' must be an integer."
            )

        return value