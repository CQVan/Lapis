import asyncio
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from lapis import Response
from lapis.protocols.http1 import HTTP1Protocol


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
    client = Client(
        b"POST / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    )
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
