"""Tests for WebApp server coverage."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nexus.webapp.server import create_app


@pytest.fixture
def client():
    app = create_app("dummy-api-key")
    return TestClient(app)


def test_webapp_index_requires_launch_token(client):
    response = client.get("/")
    assert response.status_code == 403

    token = client.app.state.web_token
    response = client.get(f"/?token={token}")
    assert response.status_code == 200
    assert "window.CSRF_TOKEN" not in response.text
    assert token not in response.text
    assert client.cookies.get("nexus_web_session") == token


def test_webapp_api_chat_unauthorized(client):
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 403


def test_webapp_api_chat_empty(client):
    token = client.app.state.web_token
    assert client.get(f"/?token={token}").status_code == 200
    response = client.post("/api/chat", json={})
    assert response.status_code == 400
    assert "Empty or invalid message" in response.json()["error"]


def test_websocket_unauthorized(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_websocket_authenticate(client):
    token = client.app.state.web_token
    assert client.get(f"/?token={token}").status_code == 200
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "authenticate", "token": ""})
        websocket.send_json({"type": "set_model", "model": "invalid-model"})
        data = websocket.receive_json()
        assert data["type"] == "error"
