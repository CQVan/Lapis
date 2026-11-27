import sys
from pathlib import Path

import pytest

from lapis import Lapis


class ScriptedClient:
    """Fake socket that replays chunks and records everything written to it."""

    def __init__(self, chunks=(), recv_error=None):
        if isinstance(chunks, (bytes, bytearray)):
            chunks = [bytes(chunks)]
        self.chunks = iter(chunks)
        self.recv_error = recv_error
        self.sent = bytearray()
        self.sent_data = self.sent
        self.closed = False
        self.timeout = None

    def recv(self, _size: int) -> bytes:
        if self.recv_error is not None:
            raise self.recv_error
        return next(self.chunks, b"")

    def sendall(self, data: bytes):
        self.sent.extend(data)

    def send(self, data: bytes):
        self.sent.extend(data)
        return len(data)

    def getpeername(self):
        return ("127.0.0.1", 0)

    def close(self):
        self.closed = True

    def settimeout(self, timeout):
        self.timeout = timeout


TestClient = ScriptedClient


@pytest.fixture
def test_client_factory():
    return ScriptedClient


@pytest.fixture
def server_factory(monkeypatch):
    def create_server(test_file, config=None):
        test_file = Path(test_file)
        if test_file.is_dir():
            test_file = test_file / "test.py"
        monkeypatch.setattr(sys, "argv", [str(test_file)])
        return Lapis(config=config)

    return create_server


@pytest.fixture
def send_request():
    def send(server, target: str, headers: str = "") -> bytes:
        request = (
            f"GET {target} HTTP/1.1\r\n" f"Host: localhost\r\n" f"{headers}" "\r\n"
        ).encode("ascii")
        client = TestClient(request)
        server._handle_request(client)
        return bytes(client.sent_data)

    return send
