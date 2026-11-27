from pathlib import Path
import sys

import pytest

from lapis import ServerConfig
from lapis.server_types import BadConfigError


def test_config_selects_api_directory(server_factory, send_request, monkeypatch):
    test_file = Path(__file__)
    monkeypatch.setattr(sys, "argv", [str(test_file)])
    config = ServerConfig.from_json("./config.json")
    server = server_factory(test_file, config=config)

    response = send_request(server, "/")

    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert response.endswith(b"API path from a different api folder!")


def test_config_rejects_values_with_the_wrong_type(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"max_request_size": "large"}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadConfigError, match='"max_request_size"'):
        ServerConfig.from_json(config_path.name)


def test_config_allows_unlimited_connections(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"max_concurrent_connections": null}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    config = ServerConfig.from_json(config_path.name)

    assert config.max_concurrent_connections is None


def test_config_allows_unlimited_request_timeout(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"request_timeout": null}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    config = ServerConfig.from_json(config_path.name)

    assert config.request_timeout is None


@pytest.mark.parametrize(
    "config_value",
    [
        '{"max_concurrent_connections": 0}',
        '{"request_timeout": 0}',
    ],
)
def test_config_rejects_non_positive_connection_limits(
    tmp_path, monkeypatch, config_value
):
    config_path = tmp_path / "config.json"
    config_path.write_text(config_value, encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadConfigError, match="greater than zero"):
        ServerConfig.from_json(config_path.name)


def test_missing_api_directory_is_rejected(tmp_path, monkeypatch):
    from lapis import Lapis
    from lapis.server_types import BadAPIDirectory

    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadAPIDirectory, match="api directory"):
        Lapis()
