from fastapi.testclient import TestClient

from companion.main import app


def test_prompt_endpoint_dispatches_supported_prompt():
    client = TestClient(app)
    response = client.post("/prompt", json={"prompt": "play"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["results"][0]["status"] == "accepted"


def test_prompt_endpoint_rejects_unsupported_prompt():
    client = TestClient(app)
    response = client.post("/prompt", json={"prompt": "compose an opera"})

    assert response.status_code == 400
    assert "supported actions" in response.json()["detail"]
