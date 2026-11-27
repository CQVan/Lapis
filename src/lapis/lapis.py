"""
The main script for the Lapis server handling initial request handling and response
"""

import asyncio
import inspect
import select
import socket
import pathlib
import runpy
import sys
import re
from threading import BoundedSemaphore, Thread
from datetime import datetime
from typing import Optional

from lapis.protocols.websocket import WebSocketProtocol
from lapis.protocols.http1 import HTTP1Protocol, Request, Response
from .server_types import (
    BadAPIDirectory,
    BadConfigError,
    BadRequest,
    Protocol,
    ServerConfig,
    ProtocolEndpointError,
)


class Lapis:
    """
    The Lapis class implements the centeral object used to run a Lapis REST server
    """

    cfg: ServerConfig

    __s: Optional[socket.socket]
    __paths: dict
    __taken_endpoints: list[str]
    __protocols: list[type[Protocol]]

    __slug_pattern = re.compile(r"\[[^\]]+\]")
    __path_pattern = re.compile(r"^\/([a-zA-Z0-9._-]+)(\/[a-zA-Z0-9._-]+)*$")

    __running: bool

    def __init__(self, config: Optional[ServerConfig] = None):
        self.cfg = config if config is not None else ServerConfig()
        self.__s = None
        self.__paths = {}
        self.__taken_endpoints = []
        self.__protocols = []
        self.__running = False

        self.__register_protocol(HTTP1Protocol)
        self.__register_protocol(WebSocketProtocol)

        self._bake_paths()
        print(self.__paths)

    def run(self, ip: str, port: int):
        """
        Starts the Lapis server to listen on a given ip and port

        :param ip: The ip for the server to listen on
        :type ip: str
        :param port: The port for the server to listen on
        :type port: int
        """
        self.cfg.validate()
        self.__s = socket.socket()
        self.__s.bind((ip, port))
        self.__s.listen()

        self.__running = True
        print(f"{self.cfg.server_name} is now listening on http://{ip}:{port}")
        connection_slots = (
            BoundedSemaphore(self.cfg.max_concurrent_connections)
            if self.cfg.max_concurrent_connections is not None
            else None
        )

        try:
            while True:
                readable, _, _ = select.select([self.__s], [], [], 0.1)
                if self.__s in readable:
                    self._accept_request(connection_slots)
        except KeyboardInterrupt:
            pass
        finally:
            self.__close()

    def _accept_request(self, connection_slots: Optional[BoundedSemaphore]):
        if connection_slots is not None:
            connection_slots.acquire()
        client = None
        try:
            client, _ = self.__s.accept()
            client.settimeout(self.cfg.request_timeout)
            Thread(
                target=self._handle_request,
                args=(client, connection_slots),
                daemon=True,
            ).start()
        except BaseException:
            try:
                if client is not None:
                    client.close()
            finally:
                if connection_slots is not None:
                    connection_slots.release()
            raise

    def __register_protocol(self, protocol: type[Protocol]):

        if self.__running:
            raise RuntimeError("Cannot register new Protocol while server is running")

        endpoints: list[str] = protocol().get_target_endpoints()
        if bool(set(endpoints) & set(self.__taken_endpoints)):
            raise ProtocolEndpointError("Cannot reuse target endpoint method!")

        self.__protocols.insert(0, protocol)
        self.__taken_endpoints.extend(endpoints)

    def register_protocol(self, protocol: type[Protocol]):
        """
        Registers new protocol for the server to use to communicate with clients

        Cannot be called while server is running

        :param protocol: Description
        :type protocol: type[Protocol]
        """
        self.__register_protocol(protocol=protocol)

        self._bake_paths()

    def _get_dynamic_dirs(self, directory: pathlib.Path):
        return [
            p
            for p in directory.iterdir()
            if p.is_dir() and p.name.startswith("[") and p.name.endswith("]")
        ]

    def __validate_path(self, relative_path: pathlib.Path):
        posix_rel = relative_path.as_posix()

        slugs = self.__slug_pattern.findall(posix_rel)
        if len(slugs) != len(set(slugs)):
            raise BadAPIDirectory(f"Endpoint contains duplicate slugs: {posix_rel}")

        clean_path_str = relative_path.with_suffix("").as_posix()
        deslugged_path = "/" + self.__slug_pattern.sub("s", clean_path_str)

        if not self.__path_pattern.match(deslugged_path):
            raise BadAPIDirectory(f"Invalid characters or format in path: {posix_rel}")

    def _bake_paths(self):
        server_path = pathlib.Path(sys.argv[0]).resolve()
        # Simplified path joining
        root = server_path.parent / self.cfg.api_directory

        try:
            root.parent.resolve(strict=False)
        except (OSError, RuntimeError) as err:
            raise BadConfigError(
                f'"{self.cfg.api_directory}" in config must be a valid file path'
            ) from err

        if not root.exists():
            raise BadAPIDirectory(f'api directory "{root}" does not exist')

        self.__paths = {}
        endpoint_paths: dict[str, pathlib.Path] = {}

        for path in root.rglob(f"{self.cfg.path_script_name}.py"):
            if not path.is_file():
                continue

            relative_path = path.relative_to(root)

            # Will raise exception if invalid path
            self.__validate_path(relative_path)

            normalized_path = self.__slug_pattern.sub(
                "[slug]", relative_path.as_posix()
            )

            if normalized_path in endpoint_paths:
                raise BadAPIDirectory(
                    f"Found overlapping [slug] endpoints:\n"
                    f'  - "{relative_path}"\n'
                    f'  - "{endpoint_paths[normalized_path]}"'
                )

            endpoint_paths[normalized_path] = relative_path

            current_level = self.__paths
            for part in relative_path.parts[:-1]:
                current_level = current_level.setdefault(part, {})

            # 4. Load the script
            script_globals = runpy.run_path(str(path.absolute()))

            api_routes = {
                f"/{k}": v
                for k, v in script_globals.items()
                if k in self.__taken_endpoints
            }

            current_level.update(api_routes)

    def __has_endpoint_path(
        self, base_url: str
    ) -> tuple[Optional[dict[str, any]], dict[str, str]]:
        # Convert the URL into parts, ignoring the leading slash
        path_parts = pathlib.Path(base_url.lstrip("/")).parts

        # Start the recursive search
        return self._search_tree(self.__paths, path_parts, {})

    def _search_tree(
        self, current_level: dict, remaining_parts: tuple[str, ...], slugs: dict
    ) -> tuple[Optional[dict], dict]:

        # Base case
        if not remaining_parts:
            return (current_level, slugs) if current_level else (None, {})

        part = remaining_parts[0]
        next_parts = remaining_parts[1:]

        # Check for if no slugs
        if part in current_level:
            result, captured_slugs = self._search_tree(
                current_level[part], next_parts, slugs
            )
            if result is not None:
                return result, captured_slugs

        # There are slugs
        dynamic_keys = [
            k for k in current_level if k.startswith("[") and k.endswith("]")
        ]

        for key in dynamic_keys:
            slug_name = key.strip("[]")
            new_slugs = {**slugs, slug_name: part}

            result, captured_slugs = self._search_tree(
                current_level[key], next_parts, new_slugs
            )
            if result is not None:
                return result, captured_slugs

        return None, {}

    @staticmethod
    def _get_content_length(data: bytes) -> int:
        headers = data.split(b"\r\n\r\n", 1)[0]
        for line in headers.split(b"\r\n")[1:]:
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            if key.strip().lower() == b"content-length":
                try:
                    content_length = int(value.strip())
                except ValueError as exc:
                    raise BadRequest("Invalid Content-Length") from exc
                if content_length < 0:
                    raise BadRequest("Invalid Content-Length")
                return content_length
        return 0

    def _read_request_data(self, client: socket.socket) -> Optional[bytes]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(self.cfg.max_request_size)
            if not chunk:
                break
            data += chunk
            if len(data) > self.cfg.max_request_size:
                self.__send_response(
                    client, Response(status_code=413, body="Request Entity Too Large")
                )
                return None

        if b"\r\n\r\n" in data:
            content_length = self._get_content_length(data)
            header_end = data.find(b"\r\n\r\n") + 4
            if header_end - 4 + content_length > self.cfg.max_request_size:
                self.__send_response(
                    client, Response(status_code=413, body="Request Entity Too Large")
                )
                return None

            while len(data) - header_end < content_length:
                chunk = client.recv(self.cfg.max_request_size)
                if not chunk:
                    break
                data += chunk
                if len(data) > self.cfg.max_request_size:
                    self.__send_response(
                        client,
                        Response(status_code=413, body="Request Entity Too Large"),
                    )
                    return None

        return data

    def _dispatch_request(self, client: socket.socket, data: bytes, request: Request):
        endpoint, request.slugs = self.__has_endpoint_path(request.base_url)

        if endpoint is None:
            raise FileNotFoundError()

        # Finds the correct protocol based on the initial request
        for protocol_cls in self.__protocols:
            protocol: Protocol = protocol_cls()
            protocol.config = self.cfg.protocol_configs.get(
                protocol.get_config_key(), {}
            )

            if not protocol.identify(initial_data=data):
                continue

            if not protocol.handshake(client=client):
                raise BadRequest("Failed Handshake with protocol!")

            target_endpoints = protocol.get_target_endpoints()

            endpoints = {
                f"/{key}": endpoint[f"/{key}"]
                for key in target_endpoints
                if f"/{key}" in endpoint
            }

            endpoints = {key.lstrip("/"): value for key, value in endpoints.items()}

            if inspect.iscoroutinefunction(protocol.handle):
                asyncio.run(
                    protocol.handle(
                        client=client,
                        slugs=request.slugs,
                        endpoints=endpoints,
                    )
                )
            else:
                protocol.handle(
                    client=client,
                    slugs=request.slugs,
                    endpoints=endpoints,
                )

            break
        else:  # No Protocol was found to be compatible
            raise BadRequest("No Compatible Protocol Found!")

    def _handle_request(self, client: socket.socket, connection_slots=None):
        try:
            data = self._read_request_data(client)
            if data is None:
                return
            request: Request = Request(data)
            self._dispatch_request(client, data, request)

        except (TimeoutError, socket.timeout):
            response = Response(status_code=408, body="408 Request Timeout")
            self.__send_response(client, response)

        except BadRequest:
            response: Response = Response(status_code=400, body="400 Bad Request")
            self.__send_response(client=client, response=response)

        except FileNotFoundError:
            response: Response = Response(status_code=404, body="404 Not Found")
            self.__send_response(client, response)

        except RuntimeError as e:
            print(f"Error handling request: {e}")
            response = Response(status_code=500, body="Internal Server Error")
            self.__send_response(client, response)

        finally:
            try:
                client.close()
            finally:
                if connection_slots is not None:
                    connection_slots.release()

    def __send_response(self, client: socket.socket, response: Response):
        client.sendall(response.to_bytes())
        current_time = datetime.now().strftime("%H:%M:%S")
        ip, _ = client.getpeername()
        print(f"{current_time} {response.status_code.value} -> {ip}")

    def __close(self):
        if self.__s is not None:
            try:
                print("Closing Server...")
                self.__running = False
                self.__s.close()
            except socket.error as e:
                print(f"Error when closing socket: {e}")
