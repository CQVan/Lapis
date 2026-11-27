from pathlib import Path

import pytest

from lapis import Request, Response, ServerConfig
from lapis.protocols.http1 import HTTP1Protocol
from lapis.server_types import BadRequest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "basic_load"


def test_reads_request_headers_in_multiple_tcp_reads(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(
        [
            b"GET / HTTP/1.1\r\n",
            b"host: localhost\r\n\r\n",
        ]
    )
    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 200 OK\r\n")


def test_unknown_http_method_is_a_bad_request(server_factory, test_client_factory):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(b"FOO / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_malformed_request_line_is_a_bad_request(server_factory, test_client_factory):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(b"GET /\r\n\r\n")

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


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


def test_http_request_rejects_bad_protocol_url_and_incomplete_request():
    with pytest.raises(BadRequest, match="Unsupported protocol"):
        Request(b"GET / FTP/1.0\r\nHost: localhost\r\n\r\n")

    with pytest.raises(BadRequest, match="Bad URL"):
        Request(b"GET http://[::1 HTTP/1.1\r\nHost: localhost\r\n\r\n")

    with pytest.raises(BadRequest, match="Malformed HTTP request"):
        Request(b"GET / HTTP/1.1\r\nHost: localhost")

    protocol = HTTP1Protocol()
    assert not protocol.identify(b"GET / HTTP/1.1\r\nmalformed\r\n\r\n")


def test_http_request_rejects_decode_errors_as_bad_request():
    class UndecodableData:
        def decode(self, _encoding):
            raise UnicodeDecodeError("iso-8859-1", b"\xff", 0, 1, "invalid")

    with pytest.raises(BadRequest, match="Invalid Encoding") as exc_info:
        Request(UndecodableData())

    assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)


def test_http_request_properties_and_headers():
    request = Request(
        b"POST /api?x=1 HTTP/1.1\r\nHost: localhost\r\nX-Test: yes\r\n\r\npayload"
    )

    assert request.protocol == "HTTP/1.1"
    assert request.base_url == "/api"
    assert request.headers["x-test"] == "yes"
    assert request.body == "payload"


def test_json_response_sets_content_type_and_serializes_unicode():
    response = Response.json({"name": "café"})
    headers, body = response.to_bytes().split(b"\r\n\r\n", 1)

    assert b"Content-Type: application/json" in headers
    assert body == b'{"name": "caf\\u00e9"}'


def test_invalid_json_body_is_a_bad_request():
    request = Request(b"POST / HTTP/1.1\r\nHost: localhost\r\n\r\n{")

    with pytest.raises(BadRequest, match="Invalid JSON body"):
        request.json()


def test_rejects_invalid_content_length(server_factory, test_client_factory):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(
        b"POST / HTTP/1.1\r\n" b"Host: localhost\r\n" b"Content-Length: nope\r\n\r\n"
    )
    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_conflicting_content_lengths_are_rejected(server_factory):
    server = server_factory(FIXTURE_DIR)

    with pytest.raises(BadRequest, match="Invalid"):
        server._get_content_length(
            b"POST / HTTP/1.1\r\n"
            b"Content-Length: 10\r\n"
            b"Content-Length: 11\r\n\r\n"
        )


def test_content_length_helper_parses_length_and_defaults_to_zero(server_factory):
    server = server_factory(FIXTURE_DIR)

    data = b"POST / HTTP/1.1\r\nX-Test: yes\r\nContent-Length: 7\r\n\r\n"
    assert server._get_content_length(data) == 7
    assert server._get_content_length(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n") == 0


def test_read_request_data_collects_body_or_rejects_truncation(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR, ServerConfig(max_request_size=100))
    header = b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\n"

    complete = test_client_factory([header, b"hello"])
    assert server._read_request_data(complete) == header + b"hello"

    incomplete = test_client_factory([header, b"abc", b""])
    assert server._read_request_data(incomplete) is None
    assert bytes(incomplete.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_read_request_data_rejects_request_exceeding_limit_during_body_read(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR, ServerConfig(max_request_size=100))
    client = test_client_factory(
        [
            b"POST / HTTP/1.1\r\n" b"Host: localhost\r\n" b"Content-Length: 20\r\n\r\n",
            b"x" * 70,
        ]
    )

    assert server._read_request_data(client) is None
    assert bytes(client.sent_data).startswith(b"HTTP/1.1 413 ")


def test_rejects_requests_over_request_limit(server_factory, test_client_factory):
    server = server_factory(FIXTURE_DIR, ServerConfig(max_request_size=20))

    oversized_header = test_client_factory(b"G" * 21)
    server._handle_request(oversized_header)
    assert bytes(oversized_header.sent_data).startswith(b"HTTP/1.1 413 ")

    oversized_body = test_client_factory(
        b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 20\r\n\r\n123456789"
    )
    server._handle_request(oversized_body)
    assert bytes(oversized_body.sent_data).startswith(b"HTTP/1.1 413 ")


def test_eof_before_request_headers_is_malformed(server_factory, test_client_factory):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(b"")
    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_rejects_request_with_negative_content_length(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(
        b"POST / HTTP/1.1\r\n" b"Host: localhost\r\n" b"Content-Length: -1\r\n\r\n"
    )

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


@pytest.mark.parametrize(
    "raw_request",
    [
        b"GET / HTTP/1.1\r\nmalformed-header\r\nHost: localhost\r\n\r\n",
        b"GET / HTTP/1.1\r\n\r\n",
    ],
)
def test_rejects_malformed_or_missing_request_headers(
    server_factory, test_client_factory, raw_request
):
    server = server_factory(FIXTURE_DIR)
    client = test_client_factory(raw_request)

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_rejects_request_larger_than_configured_limit(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR, ServerConfig(max_request_size=4))
    client = test_client_factory(
        b"POST / HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 10\r\n\r\n"
        b"1234567890"
    )

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 413 ")


def test_declared_body_length_over_limit_is_rejected(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR, ServerConfig(max_request_size=100))
    client = test_client_factory(
        b"POST / HTTP/1.1\r\n" b"Host: localhost\r\n" b"Content-Length: 200\r\n\r\n"
    )

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 413 ")
