from http import HTTPMethod, HTTPStatus
from dataclasses import dataclass
import socket
from typing import AsyncGenerator, Callable
from urllib.parse import parse_qsl, urlparse

from lapis.util import read_exact


@dataclass
class RequestHeader:
    """
    Utility class to store the request header data
    """

    method: HTTPMethod
    base_url: str
    query_params: dict[str, str]
    headers: dict[str, str]
    protocol: str


class BadHTTPRequest(Exception):
    """
    Exception raised when a malformed HTTP request is received
    """

    def __init__(
        self, message: str, status_code: int | HTTPStatus = HTTPStatus.BAD_REQUEST
    ):
        self.status_code = (
            status_code
            if isinstance(status_code, HTTPStatus)
            else HTTPStatus(status_code)
        )
        super().__init__(message)
        self


class Request:
    """
    The object class for handling HTTP 1/1.1 requests from clients
    """

    def __init__(self, data: bytes):
        try:
            text = data.decode("iso-8859-1")
        except UnicodeDecodeError as err:
            raise BadHTTPRequest("Invalid encoding") from err

        if "\r\n\r\n" not in text:
            raise BadHTTPRequest("Malformed HTTP request")

        head, self.__body = text.split("\r\n\r\n", 1)
        lines = head.split("\r\n")

        method_str, url, protocol = lines[0].split(" ", 2)
        if protocol not in ("HTTP/1.0", "HTTP/1.1"):
            raise BadHTTPRequest("Unsupported protocol")

        headers_dict = {}
        for line in lines[1:]:
            if ":" not in line:
                raise BadHTTPRequest("Malformed header")
            key, value = line.split(":", 1)
            headers_dict[key.strip()] = value.strip()

        if protocol == "HTTP/1.1" and "Host" not in headers_dict:
            raise BadHTTPRequest("Missing Host header")

        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise BadHTTPRequest("Bad URL") from exc

        self.__header_data = RequestHeader(
            method=HTTPMethod[method_str.upper()],
            base_url=parsed.path,
            query_params=dict(parse_qsl(parsed.query)),
            headers=headers_dict,
            protocol=protocol,
        )

        self.cookies = {}
        self.slugs = {}

    @classmethod
    def from_socket(cls, client: socket.socket):
        """
        Reads the initial request data from the client socket and constructs a Request object
        """
        data = bytearray()
        header_count = -1  # Start at -1 to account for the request line

        while not data.endswith(b"\r\n\r\n"):

            byte: bytes = read_exact(client, 1)

            data.extend(byte)

            if data.endswith(b"\r\n"):
                header_count += 1
                if header_count > 100:
                    raise BadHTTPRequest(
                        "Too many headers", HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE
                    )

        # Extract Content-Length directly to avoid constructing Request twice
        raw_head = bytes(data).decode("iso-8859-1")
        content_length_str = next(
            (
                line.split(":", 1)[1].strip()
                for line in raw_head.splitlines()
                if line.lower().startswith("content-length:")
            ),
            None,
        )

        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
            except ValueError as e:
                raise BadHTTPRequest(
                    "Invalid Content-Length", HTTPStatus.BAD_REQUEST
                ) from e

            if content_length < 0:
                raise BadHTTPRequest("Negative Content-Length", HTTPStatus.BAD_REQUEST)

            if content_length > 10000000:
                raise BadHTTPRequest(
                    f"Body exceeds maximum allowed size of {10000000} bytes",
                    HTTPStatus.CONTENT_TOO_LARGE,
                )

            body = read_exact(client, content_length)
            return cls(bytes(data) + body)

        return cls(bytes(data))

    @property
    def raw_data(self) -> bytes:
        """
        Returns the raw byte data of the HTTP request as received from the client
        """
        return (
            self.__header_data.method.name.encode("utf-8")
            + b" "
            + self.__header_data.base_url.encode("utf-8")
            + b" "
            + self.__header_data.protocol.encode("utf-8")
            + b"\r\n"
            + b"\r\n".join(
                f"{k}: {v}".encode("utf-8")
                for k, v in self.__header_data.headers.items()
            )
            + b"\r\n\r\n"
            + self.__body.encode("utf-8")
        )

    @property
    def method(self) -> HTTPMethod:
        """
        Returns the HTTP method (e.g., GET, POST) of the request.
        """
        return self.__header_data.method

    @property
    def protocol(self) -> str:
        """
        Returns the HTTP protocol version (e.g., 'HTTP/1.1').
        """
        return self.__header_data.protocol

    @property
    def headers(self) -> dict[str, str]:
        """
        Returns a dictionary of the HTTP headers sent by the client.
        Keys are case-sensitive as parsed from the request.
        """
        return self.__header_data.headers

    @property
    def base_url(self) -> str:
        """
        Returns the path component of the requested URL (e.g., '/api/users').
        """
        return self.__header_data.base_url

    @property
    def query_params(self) -> dict[str, str]:
        """
        Returns a dictionary containing the URL query string parameters.
        """
        return self.__header_data.query_params

    @property
    def body(self) -> str:
        """
        Returns the raw entity body of the HTTP request as a string.
        """
        return self.__body


class Response:
    """
    The object class for forming a HTTP 1/1.1 response to the client from the server
    """

    def __init__(
        self,
        status_code: int | HTTPStatus = HTTPStatus.OK,
        body: str = "",
        headers: dict[str, any] = None,
    ):
        self.status_code = (
            status_code
            if isinstance(status_code, HTTPStatus)
            else HTTPStatus(status_code)
        )

        self.protocol = "HTTP/1.1"
        self.headers = (
            headers
            if headers is not None
            else {
                "Content-Type": "text/plain",
            }
        )
        self.cookies = {}
        self.body = body

    @property
    def reason_phrase(self):
        """
        Returns the reasoning behind the status code
        """
        return self.status_code.phrase

    def to_bytes(self):
        """
        Returns the raw byte format of the Response class
        """
        body_bytes = self.body.encode("utf-8")
        if "Content-Length" not in self.headers:
            self.headers["Content-Length"] = len(body_bytes)

        response_line = (
            f"{self.protocol} {self.status_code.value} {self.reason_phrase}\r\n"
        )
        headers = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
        cookies = "".join(f"Set-Cookie: {k}={v}\r\n" for k, v in self.cookies.items())

        return (response_line + headers + cookies + "\r\n").encode("utf-8") + body_bytes


class StreamedResponse(Response):
    """
    A variant of the Response class that allows the server to stream back a response to the client
    """

    def __init__(
        self,
        stream: Callable[[Request], AsyncGenerator[bytes, None]],
        status_code=HTTPStatus.OK,
        headers: dict[str, str] = None,
    ):

        super().__init__(status_code, "", headers)

        self.stream = stream

        self.headers["Transfer-Encoding"] = "chunked"

    def get_head(self) -> bytes:
        """
        :return: The inital head of the streamed response from the server
        :rtype: bytes
        """
        response_line = (
            f"{self.protocol} {self.status_code.value} {self.reason_phrase}\r\n"
        )
        headers = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
        cookies = "".join(f"Set-Cookie: {k}={v}\r\n" for k, v in self.cookies.items())

        return (response_line + headers + cookies + "\r\n").encode("utf-8")
