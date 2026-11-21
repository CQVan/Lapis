from lapis.protocols.websocket import WSFrame, WSOpcode, WebSocketProtocol


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
