from pathlib import Path


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
