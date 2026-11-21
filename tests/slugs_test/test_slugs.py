from pathlib import Path


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
