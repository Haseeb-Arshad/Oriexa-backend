from fastapi.testclient import TestClient
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

from app.main import app


def test_external_mcp_http_initialize_sets_session_id():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[-1],
            "capabilities": {},
            "clientInfo": {
                "name": "pytest-mcp-client",
                "version": "0.1.0",
            },
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/mcp/v2/",
            json=payload,
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("mcp-session-id")
    assert "text/event-stream" in response.headers.get("content-type", "")
