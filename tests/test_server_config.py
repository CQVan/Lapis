from pathlib import Path
import sys

import pytest

from lapis import ServerConfig
from lapis.server_types import BadConfigError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "server_config"
TEST_FILE = FIXTURE_DIR / "test.py"


def test_config_selects_api_directory(server_factory, send_request, monkeypatch):
    test_file = TEST_FILE
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


def test_config_rejects_non_positive_max_concurrent_connections(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"max_concurrent_connections": 0}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadConfigError, match="greater than zero"):
        ServerConfig.from_json(config_path.name)


def test_config_rejects_non_positive_request_timeout(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"request_timeout": 0}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadConfigError, match="greater than zero"):
        ServerConfig.from_json(config_path.name)


def test_missing_api_directory_is_rejected(tmp_path, monkeypatch):
    from lapis import Lapis
    from lapis.server_types import BadAPIDirectory

    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    with pytest.raises(BadAPIDirectory, match="api directory"):
        Lapis()


def test_api_directory_skips_matching_directory(tmp_path, monkeypatch, send_request):
    from lapis import Lapis

    (tmp_path / "api" / "path.py").mkdir(parents=True)
    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])

    server = Lapis()
    response = send_request(server, "/")

    assert response.startswith(b"HTTP/1.1 404 Not Found\r\n")


@pytest.mark.parametrize(
    "resolve_error", [OSError("unresolved"), RuntimeError("unresolved")]
)
def test_api_directory_path_resolution_errors_are_rejected(
    tmp_path, monkeypatch, resolve_error
):
    from lapis import Lapis

    monkeypatch.setattr("sys.argv", [str(tmp_path / "test.py")])
    original_resolve = Path.resolve

    def fail_api_directory_resolution(path, *args, **kwargs):
        if kwargs.get("strict") is False:
            raise resolve_error
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_api_directory_resolution)

    with pytest.raises(
        BadConfigError, match='"./api" in config must be a valid file path'
    ) as exc_info:
        Lapis()

    assert exc_info.value.__cause__ is resolve_error


@pytest.mark.parametrize(
    ("value", "expected_type", "expected"),
    [
        ({"enabled": True}, dict, True),
        (["GET"], list, True),
        ({"enabled": True}, list, False),
        ("localhost", str, True),
        (3, int, True),
    ],
)
def test_config_type_checks_include_collections(value, expected_type, expected):
    from lapis.server_types import ServerConfig

    assert ServerConfig._check_type(value, expected_type) is expected


def test_config_type_checks_optional_and_union_types():
    from typing import Union

    from lapis.server_types import ServerConfig

    assert ServerConfig._check_type(None, Union[int, None])
    assert ServerConfig._check_type(3, Union[int, None])
    assert not ServerConfig._check_type("3", Union[int, None])


def test_config_type_checks_generic_container_types():
    from typing import Dict, List, Tuple

    from lapis.server_types import ServerConfig

    assert ServerConfig._check_type({"x": "y"}, Dict[str, str])
    assert ServerConfig._check_type(["GET"], List[str])
    assert ServerConfig._check_type(("x", "y"), Tuple[str, str])
