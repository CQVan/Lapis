import asyncio

import pytest

from lapis.protocols.websocket import (
    WSPortal,
    WSFrame,
    WSOpcode,
    WebSocketProtocol,
)


def test_parses_masked_text_frame():
    payload = b"hello"
    mask = b"\x01\x02\x03\x04"
    masked_payload = bytes(
        value ^ mask[index % 4] for index, value in enumerate(payload)
    )
    frame = WSFrame(
        bytes([0x80 | WSOpcode.TEXT.value, 0x80 | len(payload)]) + mask + masked_payload
    )

    assert frame.fin
    assert frame.masked
    assert frame.opcode is WSOpcode.TEXT
    assert frame.data == "hello"


def test_performs_websocket_handshake(test_client_factory):
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()
    request_data = (
        "GET / HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n\r\n"
    ).encode("ascii")

    assert protocol.identify(request_data)
    assert protocol.handshake(client)

    response = bytes(client.sent_data)
    assert response.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in response


def test_rejects_invalid_websocket_key(test_client_factory):
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()
    request_data = (
        "GET / HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Key: invalid\r\n\r\n"
    ).encode("ascii")

    assert protocol.identify(request_data)
    assert not protocol.handshake(client)
    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_websocket_identify_accepts_lowercase_header_names(test_client_factory):
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()
    request_data = (
        "GET / HTTP/1.1\r\n"
        "host: localhost\r\n"
        "connection: keep-alive, Upgrade\r\n"
        "upgrade: websocket\r\n"
        "sec-websocket-version: 13\r\n"
        "sec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
    ).encode("ascii")

    assert protocol.identify(request_data)
    assert protocol.handshake(client)


def test_websocket_rejects_unknown_http_method(test_client_factory):
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()
    request_data = b"FOO / HTTP/1.1\r\nhost: localhost\r\n\r\n"

    assert not protocol.identify(request_data)


@pytest.mark.parametrize(
    ("opcode", "parts", "expected"),
    [
        (WSOpcode.TEXT, ("Hel", "lo"), "Hello"),
        (WSOpcode.BINARY, (b"\x00\x01", b"\xfe\xff"), b"\x00\x01\xfe\xff"),
    ],
)
def test_receives_fragmented_websocket_frames(test_client_factory, opcode, parts, expected):
    async def receive():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()

        first_payload = parts[0].encode() if opcode is WSOpcode.TEXT else parts[0]
        continuation_payload = parts[1].encode() if opcode is WSOpcode.TEXT else parts[1]

        portal._WSPortal__recv_queue.put_nowait(
            WSFrame(bytes([opcode.value, len(first_payload)]) + first_payload)
        )
        portal._WSPortal__recv_queue.put_nowait(
            WSFrame(
                bytes([0x80 | WSOpcode.CONTINUATION.value, len(continuation_payload)])
                + continuation_payload
            )
        )

        return await portal.recv()

    assert asyncio.run(receive()) == expected


def test_reader_ignores_unsolicited_pong(test_client_factory):
    async def read():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__pong_waiters = None

        reads = iter(
            [
                b"\x8a\x80",
                b"\x00\x00\x00\x00",
                b"",
            ]
        )

        async def fake_read_exact(_size):
            value = next(reads, None)
            if value is None:
                raise ConnectionResetError()
            return value

        portal._WSPortal__read_exact = fake_read_exact
        await portal._WSPortal__reader()

    with pytest.raises(ConnectionResetError):
        asyncio.run(read())
