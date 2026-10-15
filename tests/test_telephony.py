from fastapi.testclient import TestClient

from app.main import create_app


def test_exotel_passthru_acknowledges_callback() -> None:
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?callsid=demo-call-001&direction=inbound"
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "provider": "exotel",
        "call_sid": "demo-call-001",
    }


def test_exotel_passthru_accepts_exotel_parameter_casing() -> None:
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?CallSid=exotel-call-001&Direction=incoming"
        )

    assert response.status_code == 200
    assert response.json()["call_sid"] == "exotel-call-001"


def test_exotel_passthru_requires_call_sid() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/telephony/exotel/passthru")

    assert response.status_code == 422
    assert response.json() == {"detail": "callsid required"}
