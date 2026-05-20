"""
Testes de contrato para o endpoint POST /v1/customers.

A camada de use case é substituída por um mock via dependency_override
para que os testes não dependam de banco de dados real.
"""
import pytest

from app.domain.enums.system import System
from app.web.main import app
from app.web.routes.customers import get_create_customer_use_case


# ---------------------------------------------------------------------------
# Mock do use case
# ---------------------------------------------------------------------------

class _MockCreateCustomer:
    """Simula CreateCustomer.execute() retornando um cus_id fixo."""

    async def execute(self, dto, system, gateway_provider):
        return "cus_000005113076"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def customer_client(fake_redis):
    mock_uc = _MockCreateCustomer()
    app.dependency_overrides[get_create_customer_use_case] = lambda: mock_uc
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as test_client:
            app.state.redis_pool = fake_redis
            yield test_client
    finally:
        app.dependency_overrides.pop(get_create_customer_use_case, None)


def _auth_headers():
    return {
        "X-System": System.NEECTIFY_SHOP.value,
        "X-API-Key": "fake-neectify-shop-key",
    }


def _valid_payload(**overrides):
    base = {
        "nome_completo": "João Silva",
        "email": "joao@exemplo.com",
        "cpf": "390.533.447-05",
        "system_customer_id": "user_42",
        "system": "neectify_shop",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def test_create_customer_returns_provider_id(customer_client):
    response = customer_client.post(
        "/v1/customers",
        json=_valid_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 201
    assert response.json()["provider_customer_id"] == "cus_000005113076"


def test_create_customer_requires_auth(customer_client):
    response = customer_client.post(
        "/v1/customers",
        json=_valid_payload(),
    )

    assert response.status_code in {401, 422}


def test_create_customer_requires_customers_create_scope(customer_client, fake_redis):
    """Sistema sem o escopo customers:create deve receber 403."""
    from app.infra.config import InternalApiClientConfig, settings

    original = settings.INTERNAL_API_CLIENTS
    settings.INTERNAL_API_CLIENTS = {
        "neectify_shop": InternalApiClientConfig(
            api_key="fake-neectify-shop-key",
            scopes=["subscriptions:create", "jobs:read"],  # sem customers:create
        )
    }
    try:
        response = customer_client.post(
            "/v1/customers",
            json=_valid_payload(),
            headers=_auth_headers(),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
    finally:
        settings.INTERNAL_API_CLIENTS = original


def test_create_customer_rejects_system_mismatch(customer_client):
    """Sistema do header diferente do campo system no body deve retornar 403."""
    response = customer_client.post(
        "/v1/customers",
        json=_valid_payload(system="neectify_food"),  # header é neectify_shop
        headers=_auth_headers(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_create_customer_rejects_neither_cpf_nor_cnpj(customer_client):
    payload = _valid_payload()
    del payload["cpf"]

    response = customer_client.post(
        "/v1/customers",
        json=payload,
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_customer_rejects_both_cpf_and_cnpj(customer_client):
    response = customer_client.post(
        "/v1/customers",
        json=_valid_payload(cnpj="11.222.333/0001-81"),
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_customer_accepts_cnpj_instead_of_cpf(customer_client):
    payload = _valid_payload(cnpj="11.222.333/0001-81")
    del payload["cpf"]

    response = customer_client.post(
        "/v1/customers",
        json=payload,
        headers=_auth_headers(),
    )

    assert response.status_code == 201
    assert "provider_customer_id" in response.json()
