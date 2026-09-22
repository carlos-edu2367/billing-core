import pytest

from app.domain.enums.gateway_provider import GatewayProvider
from app.infra.config import Settings

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "INTERNAL_WEBHOOK_SIGNATURE": "s" * 32,
    "ASAAS_API_TOKEN": "asaas-token",
    "ASAAS_WEBHOOK_SECRET": "a" * 32,
    "MERCADOPAGO_ACCESS_TOKEN": "TEST-123",
    "MERCADOPAGO_WEBHOOK_SECRET": "m" * 64,
    "MERCADOPAGO_SUBSCRIPTION_BACK_URL": "https://app.neectify.com/billing/subscription",
}

PRODUCTION = {
    "APP_ENV": "production",
    "ASAAS_SANDBOX": False,
    "ALLOWED_INTERNAL_WEBHOOK_HOSTS": ["hooks.neectify.com"],
    "INTERNAL_API_CLIENTS": {"marketfy": {"api_key": "k", "scopes": []}},
}


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **(BASE | overrides))


def test_mercadopago_is_the_default_gateway():
    assert make_settings().DEFAULT_GATEWAY_PROVIDER == GatewayProvider.MERCADOPAGO


@pytest.mark.parametrize(
    "missing",
    ["MERCADOPAGO_ACCESS_TOKEN", "MERCADOPAGO_WEBHOOK_SECRET", "MERCADOPAGO_SUBSCRIPTION_BACK_URL"],
)
def test_mercadopago_default_requires_its_credentials(missing):
    with pytest.raises(RuntimeError, match=missing):
        make_settings(**{missing: None}).validate_runtime()


def test_asaas_default_does_not_require_mercadopago_credentials():
    make_settings(
        DEFAULT_GATEWAY_PROVIDER="asaas",
        MERCADOPAGO_ACCESS_TOKEN=None,
        MERCADOPAGO_WEBHOOK_SECRET=None,
        MERCADOPAGO_SUBSCRIPTION_BACK_URL=None,
    ).validate_runtime()


def test_production_rejects_mercadopago_test_token():
    with pytest.raises(RuntimeError, match="TEST-"):
        make_settings(**PRODUCTION).validate_runtime()


def test_production_accepts_mercadopago_live_token():
    make_settings(**(PRODUCTION | {"MERCADOPAGO_ACCESS_TOKEN": "APP_USR-123"})).validate_runtime()


def test_worker_startup_validates_runtime_configuration():
    """Regressao: validate_runtime() so era chamado por app/web/main.py, entao o
    worker subia com MERCADOPAGO_ACCESS_TOKEN vazio e so quebrava na hora de
    cobrar um cliente real ("Illegal header value b'Bearer '")."""
    import inspect

    from app.workers import worker

    source = inspect.getsource(worker)
    assert "validate_runtime()" in source, (
        "o worker precisa validar a configuracao no boot, como a API faz"
    )
