import importlib
import socket
import threading
from pathlib import Path
from threading import BoundedSemaphore
from types import SimpleNamespace

import pytest

from lapis import ServerConfig


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "basic_load"


def test_request_timeout_returns_408_and_releases_connection_slot(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR)
    # socket.timeout is a separate OSError subclass on 3.9 and an alias for
    # TimeoutError on 3.10+, so this exercises the real socket timeout path.
    client = test_client_factory(recv_error=socket.timeout)
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)

    server._handle_request(client, slots)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 408 Request Timeout\r\n")
    assert slots.acquire(blocking=False)
    slots.release()


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
    second_read_started = threading.Event()
    release_first_read = threading.Event()

    class Client:
        def __init__(self, wait_for_second_read=False, wait_for_release=False):
            self.wait_for_second_read = wait_for_second_read
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
                if self.wait_for_second_read:
                    first_read_started.set()
                    if not second_read_started.wait(timeout=2):
                        raise TimeoutError
                elif self.wait_for_release:
                    first_read_started.set()
                    if not release_first_read.wait(timeout=2):
                        raise TimeoutError
                else:
                    second_read_started.set()
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

    clients = [
        Client(
            wait_for_second_read=expected_max_active == 2,
            wait_for_release=expected_max_active == 1,
        ),
        Client(),
    ]

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
            if expected_max_active == 1:
                release_first_read.set()
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
    server = server_factory(FIXTURE_DIR, config=config)
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

    server = server_factory(FIXTURE_DIR)
    listener = Listener()
    server._Lapis__s = listener
    slots = BoundedSemaphore(1)

    with pytest.raises(OSError, match="accept failed"):
        server._accept_request(slots)

    assert slots.acquire(blocking=False)
    slots.release()


def test_settimeout_failure_closes_client_and_releases_slot(server_factory):
    class Client:
        def __init__(self):
            self.closed = False

        def settimeout(self, _timeout):
            raise OSError("timeout setup failed")

        def close(self):
            self.closed = True

    class Listener:
        def accept(self):
            return client, ("127.0.0.1", 0)

    server = server_factory(FIXTURE_DIR)
    client = Client()
    server._Lapis__s = Listener()
    slots = BoundedSemaphore(1)

    with pytest.raises(OSError, match="timeout setup failed"):
        server._accept_request(slots)

    assert client.closed
    assert slots.acquire(blocking=False)
    slots.release()


def test_close_stops_running_server(server_factory):
    class Listener:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    server = server_factory(FIXTURE_DIR)
    listener = Listener()
    server._Lapis__s = listener
    server._Lapis__running = True

    server._Lapis__close()

    assert listener.closed
    assert not server._Lapis__running


def test_close_handles_socket_error(server_factory, capsys):
    class Listener:
        def close(self):
            raise socket.error("close failed")

    server = server_factory(FIXTURE_DIR)
    listener = Listener()
    server._Lapis__s = listener

    server._Lapis__close()

    assert "close failed" in capsys.readouterr().out


def test_unexpected_endpoint_exception_returns_500(
    server_factory, test_client_factory, monkeypatch
):
    server = server_factory(FIXTURE_DIR)
    monkeypatch.setattr(
        server,
        "_dispatch_request",
        lambda client, data, request: (_ for _ in ()).throw(ValueError("boom")),
    )
    client = test_client_factory(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 500 Internal Server Error\r\n")
