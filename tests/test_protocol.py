import asyncio
from pathlib import Path

import pytest

from lapis import Lapis, Protocol, Request, ServerConfig
from lapis.server_types import ProtocolEndpointError

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "basic_load"


def test_protocol_abstract_methods_raise_not_implemented():
    protocol = object()

    with pytest.raises(NotImplementedError):
        Protocol.get_config_key(protocol)
    with pytest.raises(NotImplementedError):
        Protocol.get_target_endpoints(protocol)
    with pytest.raises(NotImplementedError):
        Protocol.identify(protocol, b"")
    with pytest.raises(NotImplementedError):
        Protocol.handshake(protocol, None)

    async def call_handle():
        with pytest.raises(NotImplementedError):
            await Protocol.handle(protocol, None, None, None)

    asyncio.run(call_handle())


def test_register_protocol_is_rejected_while_server_runs(server_factory):
    class CustomProtocol(Protocol):
        def get_config_key(self):
            return "custom"

        def get_target_endpoints(self):
            return ["CUSTOM"]

        def identify(self, initial_data):
            return False

        def handshake(self, client):
            return False

        async def handle(self, client, slugs, endpoints):
            pass

    server = server_factory(FIXTURE_DIR)
    server._Lapis__running = True

    with pytest.raises(RuntimeError, match="server is running"):
        server.register_protocol(CustomProtocol)


def test_rejects_duplicate_protocol_endpoint(tmp_path, monkeypatch):
    (tmp_path / "api").mkdir()
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    class DuplicateGetProtocol(Protocol):
        def get_config_key(self):
            return "duplicate"

        def get_target_endpoints(self):
            return ["GET"]

        def identify(self, initial_data):
            return False

        def handshake(self, client):
            return False

        async def handle(self, client, slugs, endpoints):
            pass

    server = Lapis()

    with pytest.raises(ProtocolEndpointError, match="reuse target endpoint"):
        server.register_protocol(DuplicateGetProtocol)


def test_register_protocol_does_not_reload_api_scripts(tmp_path, monkeypatch):
    api = tmp_path / "api"
    api.mkdir()
    counter = tmp_path / "counter.txt"
    (api / "path.py").write_text(
        "from pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        "async def GET(req):\n"
        "    return None\n"
    )
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    class CustomProtocol(Protocol):
        def get_config_key(self):
            return "custom"

        def get_target_endpoints(self):
            return ["CUSTOM"]

        def identify(self, initial_data):
            return False

        def handshake(self, client):
            return False

        async def handle(self, client, slugs, endpoints):
            pass

    server = Lapis()
    server.register_protocol(CustomProtocol)

    assert counter.read_text() == "1"


def test_registered_protocol_receives_its_config(server_factory, test_client_factory):
    class ConfigProtocol(Protocol):
        def get_config_key(self):
            return "custom"

        def get_target_endpoints(self):
            return ["CUSTOM"]

        def identify(self, initial_data):
            return True

        def handshake(self, client):
            return True

        async def handle(self, client, slugs, endpoints):
            client.received_config = self.config

    config = ServerConfig(protocol_configs={"custom": {"enabled": True}})
    server = server_factory(FIXTURE_DIR, config=config)
    server.register_protocol(ConfigProtocol)
    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    client = test_client_factory(request_data)

    server._dispatch_request(client, request_data, Request(request_data))

    assert client.received_config == {"enabled": True}


def test_sync_protocol_and_failed_handshake(server_factory, test_client_factory):
    class SyncProtocol(Protocol):
        def get_config_key(self):
            return "sync"

        def get_target_endpoints(self):
            return ["SYNC"]

        def identify(self, initial_data):
            return True

        def handshake(self, client):
            return True

        def handle(self, client, slugs, endpoints):
            client.sync_handled = True

    class RejectingProtocol(SyncProtocol):
        def handshake(self, client):
            return False

    server = server_factory(FIXTURE_DIR)
    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"

    server.register_protocol(SyncProtocol)
    client = test_client_factory(request_data)
    server._dispatch_request(client, request_data, Request(request_data))
    assert client.sync_handled

    server = server_factory(FIXTURE_DIR)
    server.register_protocol(RejectingProtocol)
    client = test_client_factory(request_data)
    server._handle_request(client)
    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")


def test_no_compatible_protocol_returns_bad_request(
    server_factory, test_client_factory
):
    server = server_factory(FIXTURE_DIR)
    server._Lapis__protocols = []
    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    client = test_client_factory(request_data)

    server._handle_request(client)

    assert bytes(client.sent_data).startswith(b"HTTP/1.1 400 Bad Request\r\n")
