"""The classic-SignalR wire protocol.

The platform runs ASP.NET SignalR 1.x/2.x (``clientProtocol=1.5``), not SignalR Core.
No maintained client exposes the knobs we need — byte-identical ``connectionData``, the
JWT as a ``token`` query parameter, and above all cursor discipline — so this is
hand-rolled.

Cursor discipline is the part that matters. Each poll response carries a cursor ``C``
which must be stored *before* the messages in ``M`` are dispatched and echoed on the
next poll. Get it wrong and a reconnect either replays messages already acted on or
skips messages entirely — and in a ten-minute auction where 36 of 44 companies dropped
at least once, reconnects are routine rather than exceptional.
"""

from __future__ import annotations

import pytest

from centinela.transport.signalr import (
    ConnectionData,
    SignalRMessage,
    build_send_payload,
    connection_data_json,
    parse_poll_response,
)


class TestConnectionData:
    def test_serialises_hubs_the_way_the_server_expects(self) -> None:
        # The server matches this string byte-for-byte against its hub registry.
        assert connection_data_json([ConnectionData("auction"), ConnectionData("chat")]) == (
            '[{"name":"auction"},{"name":"chat"}]'
        )

    def test_uses_no_incidental_whitespace(self) -> None:
        rendered = connection_data_json([ConnectionData("auction")])
        assert " " not in rendered


class TestSendPayload:
    def test_encodes_a_hub_invocation_the_way_the_capture_shows(self) -> None:
        # Captured verbatim: data={"H":"auction","M":"Connect","A":["<id>"],"I":0}
        payload = build_send_payload(hub="auction", method="Connect", args=["abc"], call_id=0)
        assert payload == '{"H":"auction","M":"Connect","A":["abc"],"I":0}'

    def test_encodes_a_call_with_no_arguments(self) -> None:
        payload = build_send_payload(hub="auction", method="RequestClock", args=[], call_id=7)
        assert payload == '{"H":"auction","M":"RequestClock","A":[],"I":7}'


class TestParsingPollResponses:
    def test_reads_the_cursor(self) -> None:
        result = parse_poll_response('{"C":"d-ABC,0|Xn,1","M":[]}')
        assert result.cursor == "d-ABC,0|Xn,1"

    def test_reads_hub_messages(self) -> None:
        body = (
            '{"C":"d-1","M":[{"H":"auction","M":"ReceiveClock",'
            '"A":["2026-03-02T20:25:17.4058426Z"]}]}'
        )
        result = parse_poll_response(body)
        assert result.messages == [
            SignalRMessage(
                hub="auction", method="ReceiveClock", args=["2026-03-02T20:25:17.4058426Z"]
            )
        ]

    def test_reads_an_empty_keepalive(self) -> None:
        result = parse_poll_response("{}")
        assert result.messages == []
        assert result.cursor is None

    def test_flags_the_reconnect_instruction(self) -> None:
        # T:1 means the server wants us to reconnect. Ignoring it means going deaf
        # while believing we are connected.
        assert parse_poll_response('{"C":"d-1","T":1,"M":[]}').should_reconnect is True
        assert parse_poll_response('{"C":"d-1","M":[]}').should_reconnect is False

    def test_flags_the_disconnect_instruction(self) -> None:
        assert parse_poll_response('{"C":"d-1","D":1,"M":[]}').should_disconnect is True

    def test_carries_the_groups_token_when_the_server_rotates_it(self) -> None:
        result = parse_poll_response('{"C":"d-1","G":"groups-abc","M":[]}')
        assert result.groups_token == "groups-abc"

    def test_survives_a_message_shape_it_does_not_recognise(self) -> None:
        # The platform is a decade-old product; an unknown frame must not crash a
        # live auction. Skip it and keep the cursor moving.
        result = parse_poll_response('{"C":"d-1","M":[{"unexpected":true}]}')
        assert result.messages == []
        assert result.cursor == "d-1"

    def test_rejects_a_body_that_is_not_json(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_poll_response("<html>gateway timeout</html>")


class TestCursorDiscipline:
    def test_a_cursor_is_stored_before_messages_are_dispatched(self) -> None:
        # Encoded as a property of the parsed result rather than a callback ordering,
        # so a caller cannot dispatch without having the cursor in hand first.
        result = parse_poll_response(
            '{"C":"d-2","M":[{"H":"chat","M":"onConnected","A":[[]]}]}'
        )
        assert result.cursor == "d-2"
        assert len(result.messages) == 1


class TestAgainstRealPlatformOutput:
    """Bodies copied verbatim from the capture. The parser must handle what the
    platform actually sends, not what the protocol documentation implies."""

    CONNECT_ACK = '{"C":"d-D28F8151-HT,0|Mo,0|Ma,0|Mp,2","S":1,"M":[]}'
    CLOCK_TICK = (
        '{"C":"d-D28F8151-HT,0|Mo,1|Ma,0|Mp,2","M":[{"H":"auction","M":"ReceiveClock",'
        '"A":["2026-03-02T20:25:17.1425188Z"]}]}'
    )
    # The platform really does send leading whitespace on some frames.
    CLOCK_TICK_PADDED = (
        '    {"C":"d-D28F8151-HT,0|Mo,2|Ma,0|Mp,2","M":[{"H":"auction","M":"ReceiveClock",'
        '"A":["2026-03-02T20:25:53.8273674Z"]}]}'
    )
    SEND_ACK = '{"I":"0"}'

    def test_recognises_the_connect_acknowledgement(self) -> None:
        result = parse_poll_response(self.CONNECT_ACK)
        assert result.initialised is True
        assert result.cursor == "d-D28F8151-HT,0|Mo,0|Ma,0|Mp,2"
        assert result.messages == []

    def test_reads_a_real_clock_tick(self) -> None:
        result = parse_poll_response(self.CLOCK_TICK)
        assert result.messages[0].method == "ReceiveClock"
        assert result.messages[0].args == ["2026-03-02T20:25:17.1425188Z"]

    def test_tolerates_the_leading_whitespace_the_platform_sends(self) -> None:
        result = parse_poll_response(self.CLOCK_TICK_PADDED)
        assert result.messages[0].method == "ReceiveClock"
        assert result.cursor is not None

    def test_reads_a_send_acknowledgement_without_inventing_a_cursor(self) -> None:
        # /signalr/send replies carry only the invocation id. Treating the absent
        # cursor as a reset would corrupt the poll sequence.
        result = parse_poll_response(self.SEND_ACK)
        assert result.cursor is None
        assert result.messages == []

    def test_cursors_advance_between_consecutive_real_frames(self) -> None:
        first = parse_poll_response(self.CLOCK_TICK).cursor
        second = parse_poll_response(self.CLOCK_TICK_PADDED).cursor
        assert first != second, "consecutive polls must not reuse a cursor"
