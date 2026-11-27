import asyncio
import importlib
from enum import Enum
from pathlib import Path
import threading
from types import SimpleNamespace
from threading import BoundedSemaphore

import pytest

from lapis import Protocol, Request, Response, ServerConfig
from lapis.protocols.http1 import HTTP1Protocol
from lapis.server_types import BadRequest


def test_dispatches_http_method_by_name(test_client_factory):
    class Method(Enum):
        GET = "method-value"

    async def get_handler(_request):
        return Response(body="GET handler")

    protocol = HTTP1Protocol()
    protocol.request = SimpleNamespace(method=Method.GET)
    client = test_client_factory(b"")

    asyncio.run(protocol.handle(client, {}, {"GET": get_handler}))

    response = bytes(client.sent_data)
    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert response.endswith(b"GET handler")


def test_loads_and_serves_api_routes(server_factory, send_request):
    server = server_factory(Path(__file__))

    response = send_request(server, "/")
    nested_response = send_request(server, "/other")

    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert response.endswith(b"Hello World!")
    assert nested_response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert nested_response.endswith(b"Other Hello World!")


def test_unknown_route_returns_not_found(server_factory, send_request):
    server = server_factory(Path(__file__))

    response = send_request(server, "/missing")

    assert response.startswith(b"HTTP/1.1 404 Not Found\r\n")


def test_known_route_rejects_unknown_method(server_factory):
    class Client:
        def __init__(self, data):
            self.data = data
            self.sent = bytearray()

        def recv(self, _size):
            data, self.data = self.data, b""
            return data

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    server = server_factory(Path(__file__))
    client = Client(b"POST / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    server._handle_request(client)

    response = bytes(client.sent)
    assert response.startswith(b"HTTP/1.1 405 Method Not Allowed\r\n")
    assert b"Allow: GET" in response


def test_reads_request_headers_in_multiple_tcp_reads(server_factory):
    class Client:
        def __init__(self):
            self.chunks = [
                b"GET / HTTP/1.1\r\n",
                b"host: localhost\r\n\r\n",
            ]
            self.sent = bytearray()

        def recv(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    server = server_factory(Path(__file__))
    client = Client()
    server._handle_request(client)

    assert bytes(client.sent).startswith(b"HTTP/1.1 200 OK\r\n")


def test_unknown_http_method_is_a_bad_request(server_factory):
    class Client:
        def __init__(self):
            self.sent = bytearray()

        def recv(self, _size):
            return b"FOO / HTTP/1.1\r\nHost: localhost\r\n\r\n"

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    server = server_factory(Path(__file__))
    client = Client()
    server._handle_request(client)

    assert bytes(client.sent).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_request_query_values_and_json_body():
    body = '{"name":"café"}'.encode("utf-8")
    request_data = (
        b"POST /?tag=blue&tag=green HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )

    request = Request(request_data)

    assert request.query_params == {"tag": ["blue", "green"]}
    assert request.json() == {"name": "café"}


def test_json_response_sets_content_type_and_serializes_unicode():
    response = Response.json({"name": "café"})
    headers, body = response.to_bytes().split(b"\r\n\r\n", 1)

    assert b"Content-Type: application/json" in headers
    assert body == b'{"name": "caf\\u00e9"}'


def test_invalid_json_body_is_a_bad_request():
    request = Request(b"POST / HTTP/1.1\r\nHost: localhost\r\n\r\n{")

    with pytest.raises(BadRequest, match="Invalid JSON body"):
        request.json()


def test_registered_protocol_receives_its_config(server_factory, test_client_factory):
    class ConfigProtocol(Protocol):
        def get_config_key(self):
            return "custom"

        def get_target_endpoints(self):
            return ["CUSTOM"]

        def identify(self, initial_data):
            return True

        def handshake(self, client):
            return True

        async def handle(self, client, slugs, endpoints):
            client.received_config = self.config

    config = ServerConfig(protocol_configs={"custom": {"enabled": True}})
    server = server_factory(Path(__file__), config=config)
    server.register_protocol(ConfigProtocol)
    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    client = test_client_factory(request_data)

    server._dispatch_request(client, request_data, Request(request_data))

    assert client.received_config == {"enabled": True}


def test_request_timeout_returns_408_and_releases_connection_slot(server_factory):
    class TimedOutClient:
        def __init__(self):
            self.sent = bytearray()

        def recv(self, _size):
            raise TimeoutError

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    server = server_factory(Path(__file__))
    client = TimedOutClient()
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)

    server._handle_request(client, slots)

    assert bytes(client.sent).startswith(b"HTTP/1.1 408 Request Timeout\r\n")
    assert slots.acquire(blocking=False)
    slots.release()


def test_rejects_request_with_negative_content_length(server_factory):
    class Client:
        def __init__(self):
            self.request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: -1\r\n\r\n"
            )
            self.sent = bytearray()

        def recv(self, _size):
            data, self.request = self.request, b""
            return data

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    server = server_factory(Path(__file__))
    client = Client()

    server._handle_request(client)

    assert bytes(client.sent).startswith(b"HTTP/1.1 400 Bad Request\r\n")


@pytest.mark.parametrize(
    "raw_request",
    [
        b"GET / HTTP/1.1\r\nmalformed-header\r\nHost: localhost\r\n\r\n",
        b"GET / HTTP/1.1\r\n\r\n",
    ],
)
def test_rejects_malformed_request_headers(
    server_factory, test_client_factory, raw_request
):
    server = server_factory(Path(__file__))
    client = test_client_factory(raw_request)

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_rejects_request_larger_than_configured_limit(server_factory):
    class Client:
        def __init__(self):
            self.request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: 10\r\n\r\n"
                b"1234567890"
            )
            self.sent = bytearray()

        def recv(self, _size):
            data, self.request = self.request, b""
            return data

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    server = server_factory(Path(__file__), ServerConfig(max_request_size=4))
    client = Client()

    server._handle_request(client)

    assert bytes(client.sent).startswith(b"HTTP/1.1 413 ")


@pytest.mark.parametrize(
    ("max_connections", "request_timeout", "expected_max_active"),
    [(1, 2, 1), (None, 2, 2), (1, None, 1)],
)
def test_server_connection_limit_and_timeout(
    server_factory, monkeypatch, max_connections, request_timeout, expected_max_active
):
    server_module = importlib.import_module("lapis.lapis")
    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    active = 0
    max_active = 0
    lock = threading.Lock()
    first_read_started = threading.Event()
    release_first_read = threading.Event()

    class Client:
        def __init__(self, wait_for_release):
            self.wait_for_release = wait_for_release
            self.timeout = None
            self.sent = bytearray()

        def settimeout(self, timeout):
            self.timeout = timeout

        def recv(self, _size):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                if self.wait_for_release:
                    first_read_started.set()
                    if not release_first_read.wait(timeout=2):
                        raise TimeoutError
                return request_data
            finally:
                with lock:
                    active -= 1

        def sendall(self, data):
            self.sent.extend(data)

        def getpeername(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    clients = [Client(True), Client(False)]

    class Listener:
        def __init__(self):
            self.accepted = 0

        def bind(self, _address):
            pass

        def listen(self):
            pass

        def accept(self):
            client = clients[self.accepted]
            self.accepted += 1
            return client, ("127.0.0.1", 0)

        def close(self):
            pass

    listener = Listener()
    select_calls = 0

    def fake_select(_readers, _writers, _errors, _timeout):
        nonlocal select_calls
        select_calls += 1
        if select_calls == 1:
            return [listener], [], []
        if select_calls == 2:
            assert first_read_started.wait(timeout=1)
            threading.Timer(0.05, release_first_read.set).start()
            return [listener], [], []
        raise KeyboardInterrupt

    real_thread = threading.Thread
    worker_threads = []

    class TrackingThread:
        def __init__(self, target, args, daemon):
            self.thread = real_thread(target=target, args=args, daemon=daemon)
            worker_threads.append(self.thread)

        def start(self):
            self.thread.start()

    config = ServerConfig(
        max_concurrent_connections=max_connections, request_timeout=request_timeout
    )
    server = server_factory(Path(__file__), config=config)
    monkeypatch.setattr(
        server_module, "socket", SimpleNamespace(socket=lambda: listener, error=OSError)
    )
    monkeypatch.setattr(server_module.select, "select", fake_select)
    monkeypatch.setattr(server_module, "Thread", TrackingThread)

    server.run("127.0.0.1", 8080)
    for worker in worker_threads:
        worker.join(timeout=1)

    assert listener.accepted == 2
    assert max_active == expected_max_active
    assert [client.timeout for client in clients] == [request_timeout, request_timeout]
    assert all(
        bytes(client.sent).startswith(b"HTTP/1.1 200 OK\r\n") for client in clients
    )


def test_accept_failure_releases_connection_slot(server_factory):
    class Listener:
        closed = False

        def accept(self):
            raise OSError("accept failed")

    server = server_factory(Path(__file__))
    listener = Listener()
    server._Lapis__s = listener
    slots = BoundedSemaphore(1)

    with pytest.raises(OSError, match="accept failed"):
        server._accept_request(slots)

    assert slots.acquire(blocking=False)
    slots.release()


def test_close_stops_running_server(server_factory):
    class Listener:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    server = server_factory(Path(__file__))
    listener = Listener()
    server._Lapis__s = listener
    server._Lapis__running = True

    server._Lapis__close()

    assert listener.closed
    assert not server._Lapis__running
