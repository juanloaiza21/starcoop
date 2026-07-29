"""Classic ASP.NET SignalR wire format (``clientProtocol=1.5``).

The platform runs the pre-Core SignalR, and no maintained client exposes what we need:
byte-identical ``connectionData``, the JWT as a ``token`` query parameter, and cursor
discipline we control. So the wire format is hand-rolled here, as pure functions with no
I/O, which is what makes it testable without touching a government server.

**Cursor discipline is the whole point.** Every poll response carries a cursor ``C``.
It must be recorded *before* the messages in ``M`` are acted on, and echoed on the next
poll. Get that wrong and a reconnect either replays messages already acted on — bidding
twice against ourselves — or skips them silently. In an auction where 36 of 44 companies
dropped at least once, reconnects are routine, so this is a hot path rather than an edge
case.

The parser is deliberately forgiving of frames it does not recognise. This is a
decade-old product; an unknown message shape must not take the bot down mid-auction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ConnectionData",
    "PollResult",
    "SignalRMessage",
    "build_send_payload",
    "connection_data_json",
    "parse_poll_response",
]


@dataclass(frozen=True, slots=True)
class ConnectionData:
    """One hub the client subscribes to."""

    name: str


def connection_data_json(hubs: list[ConnectionData]) -> str:
    """Render the ``connectionData`` query parameter.

    The server compares this string against its hub registry, so the separators matter:
    no incidental whitespace, keys in this exact order.
    """
    return json.dumps(
        [{"name": hub.name} for hub in hubs], separators=(",", ":"), ensure_ascii=False
    )


def build_send_payload(*, hub: str, method: str, args: list[Any], call_id: int) -> str:
    """Render the body of a ``/signalr/send`` call.

    Sent form-encoded as ``data=<this>``. The shape is taken verbatim from the capture:
    ``{"H":hub,"M":method,"A":[args],"I":callId}``.
    """
    return json.dumps(
        {"H": hub, "M": method, "A": args, "I": call_id},
        separators=(",", ":"),
        ensure_ascii=False,
    )


@dataclass(frozen=True, slots=True)
class SignalRMessage:
    hub: str
    method: str
    args: list[Any]


@dataclass(frozen=True, slots=True)
class PollResult:
    cursor: str | None
    """Echo this on the next poll. Record it before acting on ``messages``."""

    messages: list[SignalRMessage] = field(default_factory=list)
    should_reconnect: bool = False
    """The server sent ``T:1``. Ignoring it means going deaf while believing we are up."""

    should_disconnect: bool = False
    """The server sent ``D:1``."""

    groups_token: str | None = None
    initialised: bool = False
    """The server sent ``S:1`` in response to ``/connect``."""


def parse_poll_response(body: str) -> PollResult:
    """Parse a ``/signalr/poll`` or ``/signalr/connect`` response body.

    Raises:
        ValueError: if the body is not JSON at all. A gateway error page reaching this
            function means the transport is broken, and pretending otherwise would let
            the bot sit quietly on a dead connection.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"poll response is not valid JSON: {body[:120]!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"poll response is not a JSON object: {body[:120]!r}")

    messages: list[SignalRMessage] = []
    for frame in payload.get("M") or []:
        if not isinstance(frame, dict):
            continue
        hub, method = frame.get("H"), frame.get("M")
        if not isinstance(hub, str) or not isinstance(method, str):
            # An unrecognised frame is skipped rather than fatal. Staying connected
            # matters more than understanding every message.
            continue
        args = frame.get("A")
        messages.append(
            SignalRMessage(
                hub=hub,
                method=method,
                args=list(args) if isinstance(args, list) else [],
            )
        )

    return PollResult(
        cursor=payload.get("C") if isinstance(payload.get("C"), str) else None,
        messages=messages,
        should_reconnect=payload.get("T") == 1,
        should_disconnect=payload.get("D") == 1,
        groups_token=payload.get("G") if isinstance(payload.get("G"), str) else None,
        initialised=payload.get("S") == 1,
    )
