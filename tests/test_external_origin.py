from starlette.requests import Request

from app.routers.external import _base_origin


def _request(headers: list[tuple[bytes, bytes]], *, scheme: str = "http", server_host: str = "backend.internal", server_port: int = 80) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": scheme,
        "path": "/api/v2/external/sessions/bootstrap",
        "raw_path": b"/api/v2/external/sessions/bootstrap",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": (server_host, server_port),
    }
    return Request(scope)


def test_base_origin_prefers_forwarded_public_origin():
    request = _request(
        [
            (b"host", b"oriexa.sayings.me"),
            (b"x-forwarded-host", b"Oriexa-sigma.vercel.app"),
            (b"x-forwarded-proto", b"https"),
        ]
    )

    assert _base_origin(request) == "https://Oriexa-sigma.vercel.app"


def test_base_origin_uses_forwarded_port_when_host_has_no_port():
    request = _request(
        [
            (b"host", b"localhost"),
            (b"x-forwarded-proto", b"http"),
            (b"x-forwarded-port", b"8080"),
        ],
        server_host="localhost",
        server_port=8000,
    )

    assert _base_origin(request) == "http://localhost:8080"
