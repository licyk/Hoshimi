"""Origins and CORS preflights do not weaken the management credential boundary."""

from fastapi.testclient import TestClient

from hoshimi.api.app import create_app


def test_default_policy_rejects_cross_origin_writes(services):
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        for origin in ("https://untrusted.example", "https://localhost"):
            response = client.post("/api/v1/login/start", headers={"Origin": origin}, json={"provider": "invalid"})
            assert response.status_code == 403
            assert response.json()["code"] == "bad_origin"


def test_cors_preflight_needs_no_key_but_actual_request_does(services):
    services.settings.access_token = "secret"
    services.settings.allow_origins = ["https://ui.example"]
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.options("/api/v1/login/start", headers={"Origin": "https://ui.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://ui.example"
        assert client.post("/api/v1/login/start", headers={"Origin": "https://ui.example"}, json={"provider": "invalid"}).status_code == 401
        assert client.options("/api/v1/login/start").status_code == 401


def test_health_exemption_cannot_open_a_protected_child_route(services):
    services.settings.access_token = "secret"
    app = create_app(services, serve_ui=False)
    @app.get("/api/v1/app/health/private")
    def private():
        return {"private": True}
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/v1/app/health").status_code == 200
        assert client.get("/api/v1/app/health/private").status_code == 401
