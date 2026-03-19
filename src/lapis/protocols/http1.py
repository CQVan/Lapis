"""
Module containing the HTTP 1/1.1 protocol implementation for Lapis server
"""

from lapis.server_types import Protocol
import socket

from dataclasses import dataclass
from http import HTTPMethod

from lapis.util import print_connection_event, read_exact
from .http1_types import Request, Response, StreamedResponse, BadHTTPRequest


@dataclass
class HTTP1Config:
    """
    The class containing all configuration settings for a Lapis HTTP1.X connection
    """


class HTTP1Protocol(Protocol):
    """
    The protocol created to handle HTTP 1/1.1 communications between server and client
    """

    request: Request = None

    def __init__(self, config):
        self.__config = HTTP1Config(**config)

    def get_target_endpoints(self) -> list[str]:
        return [method.name for method in HTTPMethod]

    def identify(self, request: Request) -> bool:
        self.request = request
        return True

    def handshake(self, client: socket.socket):
        # don't know how this would create an exception but its here just to be safe

        ip, _ = client.getpeername()

        tag: str = f"{self.request.method.name} {self.request.base_url}"

        print_connection_event(tag, "->", ip)

        return True

    async def handle(self, client: socket.socket, slugs, endpoints):

        self.request.slugs = slugs

        if self.request.method in endpoints:
            response: Response = await endpoints[self.request.method](self.request)

            ip, _ = client.getpeername()

            if isinstance(response, StreamedResponse):
                client.sendall(response.get_head())

                print_connection_event(response.status_code.value, "STREAM ->", ip)

                async for packet in response.stream(self.request):
                    chunk_len = f"{len(packet):X}\r\n".encode("utf-8")
                    client.sendall(chunk_len + packet + b"\r\n")

                client.sendall(b"0\r\n\r\n")

                print_connection_event(
                    response.status_code.value, "STREAM FINISHED ->", ip
                )

            else:
                client.sendall(response.to_bytes())

                print_connection_event(response.status_code.value, "->", ip)
        else:
            raise FileNotFoundError()
