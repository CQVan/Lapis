import asyncio
from types import SimpleNamespace

import pytest

from lapis.protocols.websocket import (
    WSPortal,
    WSPortalClosedError,
    WSFrame,
    WSRecvInvalidFrameError,
    WSRecvTimeoutError,
    WSOpcode,
    WebSocketProtocol,
)


def test_portal_initialization_starts_reader(monkeypatch, test_client_factory):
    started = []

    async def reader(portal):
        started.append(portal)

    monkeypatch.setattr(WSPortal, "_WSPortal__reader", reader)
    client = test_client_factory(b"")
    blocking_modes = []
    client.setblocking = blocking_modes.append

    async def initialize():
        portal = WSPortal({"slug": "value"}, client)
        await asyncio.sleep(0)
        return portal

    portal = asyncio.run(initialize())

    assert blocking_modes == [False]
    assert portal.slugs == {"slug": "value"}
    assert started == [portal]


def test_sending_frame_on_closed_portal():
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__closed = True

    with pytest.raises(WSPortalClosedError):
        portal._WSPortal__send_frame(WSOpcode.TEXT, b"hello")


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


def test_rejects_websocket_key_with_wrong_decoded_length(test_client_factory):
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()
    request_data = (
        "GET / HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Key: eA==\r\n\r\n"
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
    "headers",
    [
        "Connection: keep-alive\r\nUpgrade: websocket\r\n",
        "Connection: Upgrade\r\nUpgrade: h2c\r\n",
    ],
)
def test_websocket_identify_rejects_non_websocket_upgrade(headers):
    protocol = WebSocketProtocol()
    request_data = ("GET / HTTP/1.1\r\nHost: localhost\r\n" f"{headers}\r\n").encode(
        "ascii"
    )

    assert not protocol.identify(request_data)


def test_websocket_protocol_exposes_config_and_target_endpoints():
    protocol = WebSocketProtocol()

    assert protocol.get_config_key() == "websocket13_config"
    assert protocol.get_target_endpoints() == ["WEBSOCKET"]


@pytest.mark.parametrize(
    ("opcode", "parts", "expected"),
    [
        (WSOpcode.TEXT, ("Hel", "lo"), "Hello"),
        (WSOpcode.TEXT, (b"caf\xc3", b"\xa9"), "café"),
        (WSOpcode.BINARY, (b"\x00\x01", b"\xfe\xff"), b"\x00\x01\xfe\xff"),
    ],
)
def test_receives_fragmented_websocket_frames(
    test_client_factory, opcode, parts, expected
):
    async def receive():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()

        def as_bytes(part):
            return part.encode() if isinstance(part, str) else part

        first_payload = as_bytes(parts[0])
        continuation_payload = as_bytes(parts[1])

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
    portals = []

    async def read():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__pong_waiters = None
        portal._WSPortal__recv_queue = asyncio.Queue()
        portals.append(portal)

        reads = iter([b"\x8a\x80", b"\x00\x00\x00\x00"])

        async def fake_read_exact(size):
            if size == 0:
                return b""
            value = next(reads, None)
            if value is None:
                raise ConnectionResetError()
            assert len(value) == size
            return value

        portal._WSPortal__read_exact = fake_read_exact
        await portal._WSPortal__reader()

    with pytest.raises(ConnectionResetError):
        asyncio.run(read())

    assert portals[0]._WSPortal__recv_queue.empty()


def test_reader_parses_16_bit_payload_length(test_client_factory):
    payload = b"x" * 126
    reads = iter([b"\x82\x7e", b"\x00\x7e", payload])
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False
    portal._WSPortal__pong_waiters = None

    async def read():
        portal._WSPortal__recv_queue = asyncio.Queue()

        async def fake_read_exact(size):
            value = next(reads, None)
            if value is None:
                raise ConnectionResetError()
            assert len(value) == size
            return value

        portal._WSPortal__read_exact = fake_read_exact
        await portal._WSPortal__reader()

    with pytest.raises(ConnectionResetError):
        asyncio.run(read())

    assert portal._WSPortal__recv_queue.get_nowait().data == payload


def test_reader_rejects_fragmented_ping(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False
    portal._WSPortal__pong_waiters = None
    reads = iter([b"\x09\x00"])
    close_codes = []

    async def fake_read_exact(size):
        if size == 0:
            return b""
        value = next(reads)
        assert len(value) == size
        return value

    def close(code=1000):
        close_codes.append(code)
        portal._WSPortal__closed = True

    portal._WSPortal__read_exact = fake_read_exact
    portal.close = close

    asyncio.run(portal._WSPortal__reader())

    assert close_codes == [1002]


def test_reader_resolves_pong_waiter_and_queues_data(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False
    reads = iter([b"\x8a\x00", b"\x81\x01", b"x"])

    async def read():
        portal._WSPortal__recv_queue = asyncio.Queue()
        portal._WSPortal__pong_waiters = asyncio.get_running_loop().create_future()

        async def fake_read_exact(size):
            if size == 0:
                return b""
            value = next(reads, None)
            if value is None:
                raise ConnectionResetError()
            assert len(value) == size
            return value

        portal._WSPortal__read_exact = fake_read_exact
        await portal._WSPortal__reader()

    with pytest.raises(ConnectionResetError):
        asyncio.run(read())

    assert portal._WSPortal__pong_waiters.result() is True
    assert portal._WSPortal__recv_queue.get_nowait().data == "x"


def test_sends_extended_websocket_payload(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False

    payload = b"x" * 126
    portal._WSPortal__send_frame(WSOpcode.BINARY, payload)

    sent = bytes(portal._WSPortal__client.sent_data)
    assert sent[:4] == b"\x82\x7e\x00\x7e"
    assert sent[4:] == payload


def test_sends_64bit_websocket_payload(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False

    payload = b"x" * 65536
    portal._WSPortal__send_frame(WSOpcode.BINARY, payload)

    sent = bytes(portal._WSPortal__client.sent_data)
    assert sent[:10] == b"\x82\x7f" + (65536).to_bytes(8, "big")
    assert sent[10:] == payload


def test_ping_timeout_returns_false(test_client_factory):
    async def ping():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__pong_waiters = None
        return await portal.ping(0.001)

    assert asyncio.run(ping()) is False


def test_receive_timeout_raises_websocket_error(test_client_factory):
    async def receive():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()
        await portal.recv(timeout=0.001)

    with pytest.raises(WSRecvTimeoutError):
        asyncio.run(receive())


def test_closed_portal_rejects_send(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = True

    with pytest.raises(WSPortalClosedError):
        portal.send("hello")


def test_close_marks_portal_closed(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False

    portal.close()
    portal.close()

    assert portal.closed
    assert b"\x88\x02\x03\xe8" in bytes(portal._WSPortal__client.sent_data)


def test_close_reads_peer_before_closing(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    client = test_client_factory(b"")

    def getpeername():
        if client.closed:
            raise OSError("socket is closed")
        return ("127.0.0.1", 0)

    client.closed = False
    client.close = lambda: setattr(client, "closed", True)
    client.getpeername = getpeername
    portal._WSPortal__client = client
    portal._WSPortal__closed = False

    portal.close()

    assert portal.closed


def test_reader_handles_ping_pong_and_close(test_client_factory):
    ping_client = test_client_factory(b"")
    close_client = test_client_factory(b"")

    def make_read_exact(chunks):
        reads = iter(chunks)

        async def fake_read_exact(size):
            if size == 0:
                return b""
            value = next(reads, None)
            if value is None:
                raise ConnectionResetError()
            assert len(value) == size
            return value

        return fake_read_exact

    async def read_ping():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = ping_client
        portal._WSPortal__closed = False
        portal._WSPortal__pong_waiters = None
        portal._WSPortal__read_exact = make_read_exact([b"\x89\x02", b"ok"])
        await portal._WSPortal__reader()

    async def read_close():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = close_client
        portal._WSPortal__closed = False
        portal._WSPortal__pong_waiters = None
        portal._WSPortal__read_exact = make_read_exact([b"\x88\x00"])
        await portal._WSPortal__reader()

    with pytest.raises(ConnectionResetError):
        asyncio.run(read_ping())
    assert b"\x8a\x02ok" in bytes(ping_client.sent_data)

    asyncio.run(read_close())


def test_ping_returns_true_when_pong_arrives(test_client_factory):
    async def ping():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False

        async def send_and_pong():
            await asyncio.sleep(0)
            portal._WSPortal__pong_waiters.set_result(True)

        asyncio.create_task(send_and_pong())
        portal._WSPortal__send_frame = lambda *args, **kwargs: None
        return await portal.ping(0.1)

    assert asyncio.run(ping()) is True


def test_sends_text_websocket_payload(test_client_factory):
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__client = test_client_factory(b"")
    portal._WSPortal__closed = False

    portal.send("hello")

    assert bytes(portal._WSPortal__client.sent_data).startswith(b"\x81\x05hello")


def test_websocket_frame_handles_truncated_extended_and_invalid_payloads():
    with pytest.raises(ValueError, match="Data too short"):
        WSFrame(b"\x80")

    frame126 = WSFrame(b"\x82\x7e" + (126).to_bytes(2, "big") + b"x" * 126)
    assert frame126.payload_length == 126
    assert frame126.masking_key is None
    assert frame126.__str__().startswith("WSFrame(")

    frame127 = WSFrame(b"\x82\x7f" + (127).to_bytes(8, "big") + b"x" * 127)
    assert frame127.payload_length == 127

    invalid_text = WSFrame(bytes([0x81, 2]) + b"\xff\xfe")
    with pytest.raises(ValueError, match="Invalid UTF-8"):
        _ = invalid_text.data


def test_websocket_masked_extended_payload_uses_extended_header():
    mask = b"\x01\x02\x03\x04"
    payload = b"x" * 127
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    frame = WSFrame(b"\x82\xff" + (127).to_bytes(8, "big") + mask + masked)

    assert frame.payload_length == 127
    assert frame.masking_key == mask
    assert frame.data == payload


def test_websocket_frame_builds_string_preview(test_client_factory):
    binary = WSFrame(bytes([0x82, 51]) + b"x" * 51)
    text = WSFrame(bytes([0x81, 51]) + b"x" * 51)

    assert str(binary).endswith(f"payload={'78' * 50}...)")
    assert str(text).endswith(f"payload={'x' * 50}...)")


def test_read_exact_wraps_socket_errors(test_client_factory, monkeypatch):
    async def read():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")

        class Loop:
            async def sock_recv(self, client, size):
                raise OSError("socket failed")

        monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
        await portal._WSPortal__read_exact(2)

    with pytest.raises(ConnectionError, match="Connection Error"):
        asyncio.run(read())


def test_read_exact_reads_complete_payload(test_client_factory, monkeypatch):
    requested_sizes = []

    async def read():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        chunks = iter([b"a", b"b"])

        class Loop:
            async def sock_recv(self, client, size):
                requested_sizes.append(size)
                return next(chunks)

        monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
        return await portal._WSPortal__read_exact(2)

    assert asyncio.run(read()) == b"ab"
    assert requested_sizes == [2, 1]


def test_read_exact_rejects_empty_chunk(test_client_factory, monkeypatch):
    async def read():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")

        class Loop:
            async def sock_recv(self, client, size):
                return b""

        monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
        await portal._WSPortal__read_exact(2)

    with pytest.raises(ConnectionError) as exc_info:
        asyncio.run(read())

    assert isinstance(exc_info.value.__cause__, ConnectionResetError)


@pytest.mark.parametrize(
    ("request_data", "expected"),
    [
        (
            b"POST / HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n",
            b"HTTP/1.1 400 Bad Request",
        ),
        (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n",
            b"HTTP/1.1 426 ",
        ),
        (
            b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n\r\n",
            b"HTTP/1.1 400 Bad Request",
        ),
    ],
)
def test_websocket_handshake_rejection_branches(
    test_client_factory, request_data, expected
):
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()

    assert protocol.identify(request_data)
    assert not protocol.handshake(client)
    assert bytes(client.sent_data).startswith(expected)


def test_websocket_handshake_rejects_missing_host(test_client_factory):
    client = test_client_factory(b"")
    protocol = WebSocketProtocol()
    protocol.inital_req = SimpleNamespace(
        method="GET",
        headers={"connection": "Upgrade", "upgrade": "websocket"},
        base_url="/",
    )

    assert not protocol.handshake(client)
    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request")


def test_websocket_handle_rejects_missing_endpoint(test_client_factory):
    async def handle():
        protocol = WebSocketProtocol()
        await protocol.handle(test_client_factory(b""), {}, {})

    with pytest.raises(FileNotFoundError, match="No Websocket Endpoint"):
        asyncio.run(handle())


def test_websocket_recv_rejects_wrong_continuation(test_client_factory):
    async def receive():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()
        portal._WSPortal__recv_queue.put_nowait(
            WSFrame(bytes([WSOpcode.TEXT.value, 1]) + b"x")
        )
        portal._WSPortal__recv_queue.put_nowait(
            WSFrame(bytes([0x80 | WSOpcode.BINARY.value, 1]) + b"\x00")
        )
        await portal.recv()

    with pytest.raises(WSRecvInvalidFrameError, match="Expected continuation"):
        asyncio.run(receive())


def test_websocket_recv_rejects_closed_portal():
    portal = WSPortal.__new__(WSPortal)
    portal._WSPortal__closed = True

    with pytest.raises(WSPortalClosedError, match="closed portal"):
        asyncio.run(portal.recv())


def test_websocket_recv_returns_unfragmented_frame(test_client_factory):
    async def receive():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()
        portal._WSPortal__recv_queue.put_nowait(WSFrame(b"\x81\x02hi"))

        return await portal.recv()

    assert asyncio.run(receive()) == "hi"


def test_websocket_recv_closes_invalid_fragmented_text(test_client_factory):
    async def receive():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = test_client_factory(b"")
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()
        portal._WSPortal__recv_queue.put_nowait(WSFrame(b"\x01\x01\xff"))
        portal._WSPortal__recv_queue.put_nowait(WSFrame(b"\x80\x00"))

        with pytest.raises(WSRecvInvalidFrameError, match="Invalid UTF-8"):
            await portal.recv()

        assert portal.closed
        assert b"\x88\x02\x03\xea" in bytes(portal._WSPortal__client.sent_data)

    asyncio.run(receive())


def test_reader_rejects_oversized_websocket_payload(test_client_factory):
    client = test_client_factory(b"")

    async def read():
        portal = WSPortal.__new__(WSPortal)
        portal._WSPortal__client = client
        portal._WSPortal__closed = False
        portal._WSPortal__recv_queue = asyncio.Queue()
        portal._WSPortal__pong_waiters = None

        payload_length = 65536
        reads = iter(
            [
                bytes([0x82, 127]),
                payload_length.to_bytes(8, "big"),
            ]
        )

        async def fake_read_exact(size):
            value = next(reads, None)
            if value is None:
                raise ConnectionResetError()
            assert len(value) == size
            return value

        portal._WSPortal__read_exact = fake_read_exact
        await portal._WSPortal__reader()

    asyncio.run(read())

    assert b"\x88\x02\x03\xf1" in bytes(client.sent_data)


def test_websocket_handle_calls_matching_endpoint(monkeypatch, test_client_factory):
    portals = []
    received = []

    class Portal:
        def __init__(self, slugs, client):
            self.slugs = slugs
            self.client = client
            portals.append(self)

    async def endpoint(portal):
        received.append(portal)

    monkeypatch.setattr("lapis.protocols.websocket.WSPortal", Portal)

    async def handle():
        await WebSocketProtocol().handle(
            test_client_factory(b""),
            {"slug": "value"},
            {"WEBSOCKET": endpoint},
        )

    asyncio.run(handle())

    assert received == portals
    assert portals[0].slugs == {"slug": "value"}
