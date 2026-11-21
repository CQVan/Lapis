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
