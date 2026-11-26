import sys

import pytest

from lapis import Lapis


class TestClient:
    def __init__(self, request_data: bytes):
        self.request_data = request_data
        self.sent_data = bytearray()

    def recv(self, _size: int) -> bytes:
        return self.request_data

    def sendall(self, data: bytes):
        self.sent_data.extend(data)

    def send(self, data: bytes):
        self.sent_data.extend(data)
        return len(data)

    def getpeername(self):
        return ("127.0.0.1", 0)

    def close(self):
        pass


@pytest.fixture
def test_client_factory():
    return TestClient


@pytest.fixture
def server_factory(monkeypatch):
    def create_server(test_file, config=None):
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
