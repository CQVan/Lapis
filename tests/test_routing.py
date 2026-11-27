import asyncio
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from lapis import Response
from lapis.protocols.http1 import HTTP1Protocol

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "basic_load"


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
    server = server_factory(FIXTURE_DIR)

    response = send_request(server, "/")
    nested_response = send_request(server, "/other")

    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert response.split(b"\r\n\r\n", 1)[1] == b"Hello World!"
    assert nested_response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert nested_response.split(b"\r\n\r\n", 1)[1] == b"Other Hello World!"


def test_unknown_route_returns_not_found(server_factory, send_request):
    server = server_factory(FIXTURE_DIR)

    response = send_request(server, "/missing")

    assert response.startswith(b"HTTP/1.1 404 Not Found\r\n")


def test_known_route_rejects_unknown_method(server_factory, test_client_factory):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(b"POST / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    server._handle_request(client)

    response = bytes(client.sent_data)
    assert response.startswith(b"HTTP/1.1 405 Method Not Allowed\r\n")
    assert b"Allow: GET" in response
