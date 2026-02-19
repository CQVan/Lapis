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
from threading import Thread
from datetime import datetime

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

    cfg: ServerConfig = ServerConfig()

    __s: socket.socket = None
    __paths: dict = {}
    __taken_endpoints: list[str] = []
    __protocols: list[type[Protocol]] = []

    __running: bool = False

    def __init__(self, config: ServerConfig | None = None):

        if config is not None:
            self.cfg = config

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
        self.__s = socket.socket()
        self.__s.bind((ip, port))
        self.__s.listen()

        self.__running = True
        print(f"{self.cfg.server_name} is now listening on http://{ip}:{port}")

        try:
            while True:
                readable, _, _ = select.select([self.__s], [], [], 0.1)
                if self.__s in readable:
                    client, _ = self.__s.accept()
                    t = Thread(target=self._handle_request, args=(client,), daemon=True)
                    t.start()
        except KeyboardInterrupt:
            pass
        finally:
            self.__close()

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

        slug_pattern = re.compile(r"\[[^\]]+\]")
        path_pattern = re.compile(r"^\/([a-zA-Z0-9._-]+)(\/[a-zA-Z0-9._-]+)*$")

        self.__paths = {}
        endpoint_paths: dict[str, pathlib.Path] = {}

        for path in root.rglob(f"{self.cfg.path_script_name}.py"):
            if not path.is_file():
                continue

            relative_path = path.relative_to(root)

            posix_rel = relative_path.as_posix()
            normalized_path = slug_pattern.sub("[slug]", posix_rel)

            if normalized_path in endpoint_paths:
                raise BadAPIDirectory(
                    f"Found overlapping [slug] endpoints:\n"
                    f'  - "{relative_path}"\n'
                    f'  - "{endpoint_paths[normalized_path]}"'
                )

            clean_path_str = relative_path.with_suffix("").as_posix()
            deslugged_path = "/" + slug_pattern.sub("s", clean_path_str)

            if not path_pattern.match(deslugged_path):
                raise BadAPIDirectory(
                    f"Invalid characters or format in path: {posix_rel}"
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
    ) -> tuple[dict[str, any] | None, dict[str, str]]:
        # Digs through api cache map to find the correct endpoint directory
        slugs = {}
        path = pathlib.Path(f"{self.cfg.api_directory}{base_url}")
        parts: list[str] = path.relative_to(self.cfg.api_directory).parts

        leaf: dict = self.__paths
        for part in parts:
            if part in leaf:
                leaf = leaf[part]
                continue

            # checks if there are dynamic routes available
            dynamic_routes: list[str] = list(
                {key for key in leaf if key.startswith("[") and key.endswith("]")}
            )

            if len(dynamic_routes) == 1:
                slugs[dynamic_routes[0].strip("[]")] = part
                leaf = leaf[dynamic_routes[0]]
            else:
                return (None, {})

        if len(leaf) == 0:
            return (None, {})

        return (leaf, slugs)

    def _handle_request(self, client: socket.socket):
        data = client.recv(self.cfg.max_request_size)

        try:
            request: Request = Request(data)

        except BadRequest:
            self.__send_response(
                client, Response(status_code=400, body="400 Bad Request")
            )
            client.close()
            return

        try:
            endpoint, request.slugs = self.__has_endpoint_path(request.base_url)

            if endpoint is None:
                raise FileNotFoundError()

            # Finds the correct protocol based on the inital request
            for protocol_cls in self.__protocols:
                protocol: Protocol = protocol_cls()

                if not protocol.identify(initial_data=data):
                    continue

                if not protocol.handshake(client=client):
                    raise BadRequest("Failed Handshake with protocol!")

                target_endpoints = protocol.get_target_endpoints()

                endpoints = {
                    f"/{k}": endpoint[f"/{k}"]
                    for k in target_endpoints
                    if f"/{k}" in endpoint
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
            client.close()

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
