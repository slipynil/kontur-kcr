from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from clients_service.config import Settings
from clients_service.main import create_app

DB_URL = "postgresql+psycopg://localhost/test"
TOKENS = {
    "gateway_service_token": "test-gateway",
    "operator_work_service_token": "test-operator-work",
    "import_service_token": "test-import-worker",
}


def test_all_business_routes_require_service_credentials():
    settings = Settings(
        database_url=DB_URL,
        enable_registry_read=True,
        enable_import_apply=True,
        enable_data_recovery=True,
        **TOKENS,
    )
    client = TestClient(create_app(settings))
    path = "/internal/v1/clients/example"
    write_routes = [
        ("POST", "/internal/v1/import/apply"),
        ("POST", f"/internal/v1/import/{uuid4()}/rollback"),
        ("DELETE", path),
        ("DELETE", f"{path}/snapshots/2026_Q1"),
    ]
    routes = [
        ("GET", "/internal/v1/clients"),
        ("GET", path),
        ("GET", f"{path}/snapshots/history"),
        *write_routes,
    ]
    for method, route in routes:
        for authorization in (None, "Basic test-gateway", "Bearer invalid"):
            headers = {"X-User-Role": "ADMIN"}
            if authorization:
                headers["Authorization"] = authorization
            response = client.request(method, route, headers=headers)
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.json()["code"] == "UNAUTHORIZED"

    for method, route in write_routes:
        for token in ("test-gateway", "test-operator-work"):
            response = client.request(method, route, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 403
            assert response.json()["code"] == "FORBIDDEN"

    response = client.get(
        path,
        headers=[("Authorization", "Bearer test-gateway"), ("Authorization", "Bearer invalid")],
    )
    assert response.status_code == 401
    assert client.get("/health/live").status_code == 200
    operations = client.get("/openapi.json").json()["paths"]
    assert operations["/internal/v1/clients"]["get"]["security"] == [{"ServiceBearer": []}]


@pytest.mark.parametrize(
    "options",
    [
        {"enable_registry_read": True},
        {"enable_import_apply": True, "gateway_service_token": "test-gateway"},
        {"enable_data_recovery": True},
        {"allow_unauthenticated_local": True, **TOKENS},
        {**TOKENS, "import_service_token": "test-gateway"},
        {"import_service_token": "test-invalid token"},
    ],
)
def test_invalid_service_configuration_hides_secrets(options):
    with pytest.raises(ValidationError) as error:
        Settings(database_url=DB_URL, **options)
    assert "test-" not in str(error.value)
