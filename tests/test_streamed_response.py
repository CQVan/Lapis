from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "streamed_response"


def test_streams_chunked_response(server_factory, send_request):
    server = server_factory(FIXTURE_DIR)

    response = send_request(server, "/")
    headers, body = response.split(b"\r\n\r\n", 1)

    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"Transfer-Encoding: chunked" in headers
    assert body == b"5\r\nHello\r\n7\r\n World!\r\n0\r\n\r\n"
