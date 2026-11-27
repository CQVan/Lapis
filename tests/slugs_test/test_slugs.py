from pathlib import Path

import pytest

from lapis import Lapis
from lapis.server_types import BadAPIDirectory, Protocol, ProtocolEndpointError


def test_resolves_slug_routes_and_captures_values(server_factory, send_request):
    server = server_factory(Path(__file__))

    cases = [
        ("/alice", b"{'slug1': 'alice'}"),
        ("/alice/other/document-42", b"{'slug1': 'alice', 'slug2': 'document-42'}"),
        ("/team/maybe/project", b"{'slug2': 'team', 'slug3': 'project'}"),
    ]

    for path, expected_slugs in cases:
        response = send_request(server, path)
        assert response.startswith(b"HTTP/1.1 200 OK\r\n")
        assert response.endswith(expected_slugs)


def test_rejects_duplicate_slugs(tmp_path, monkeypatch):
    api = tmp_path / "api"
    route = api / "[user]" / "other" / "[user]"
    route.mkdir(parents=True)
    (route / "path.py").write_text("async def GET(req):\n    return None\n")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadAPIDirectory, match="duplicate slugs"):
        Lapis()


def test_rejects_overlapping_slug_routes(tmp_path, monkeypatch):
    api = tmp_path / "api"
    first = api / "[user]" / "path.py"
    second = api / "[team]" / "path.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("async def GET(req):\n    return None\n")
    second.write_text("async def GET(req):\n    return None\n")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadAPIDirectory, match="overlapping"):
        Lapis()


def test_rejects_invalid_route_characters(tmp_path, monkeypatch):
    route = tmp_path / "api" / "bad$name"
    route.mkdir(parents=True)
    (route / "path.py").write_text("async def GET(req):\n    return None\n")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadAPIDirectory, match="Invalid characters"):
        Lapis()


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
