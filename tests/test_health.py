from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_built_frontend_serves_microphone_worklet(tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>demo</html>")
    (tmp_path / "mic-worklet.js").write_text("registerProcessor('mic-capture', MicCapture);")

    with TestClient(create_app(frontend_dist=tmp_path)) as client:
        response = client.get("/mic-worklet.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "registerProcessor" in response.text
