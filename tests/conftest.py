import os
os.environ["ASAAS_WEBHOOK_SECRET"] = "fake-asaas-webhook-secret-long-enough-32-chars"
os.environ["INTERNAL_WEBHOOK_SIGNATURE"] = "test-webhook-signature-for-dev-only-32-chars"

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from app.infra.config import InternalApiClientConfig, settings
from app.web.main import app


class FakeJob:
    def __init__(self, job_id: str):
        self.job_id = job_id


class FakeRedis:
    def __init__(self):
        self.values: dict[str, object] = {}
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self.sorted_sets: dict[str, dict[str, float]] = defaultdict(dict)
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.enqueued_jobs: list[tuple[tuple, dict]] = []
        self.job_counter = 0

    async def enqueue_job(self, *args, **kwargs):
        self.job_counter += 1
        self.enqueued_jobs.append((args, kwargs))
        return FakeJob(f"job-{self.job_counter}")

    async def setex(self, key: str, ttl: int, value):
        self.values[key] = value

    async def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def incr(self, key: str):
        current = int(self.values.get(key, 0)) + 1
        self.values[key] = current
        return current

    async def get(self, key: str):
        return self.values.get(key)

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def expire(self, key: str, ttl: int):
        return True

    async def hset(self, key: str, mapping: dict):
        self.hashes[key].update({str(k): str(v) for k, v in mapping.items()})

    async def hgetall(self, key: str):
        return self.hashes.get(key, {})

    async def zscore(self, key: str, member: str):
        return self.sorted_sets[key].get(member)

    async def zadd(self, key: str, mapping: dict[str, float]):
        self.sorted_sets[key].update(mapping)

    async def sismember(self, key: str, member: str):
        return member in self.sets[key]

    async def ping(self):
        return True

    async def close(self):
        return None

    async def aclose(self):
        return None


@pytest.fixture(autouse=True)
def fake_internal_clients(monkeypatch):
    original = settings.INTERNAL_API_CLIENTS
    original_webhook_secret = settings.ASAAS_WEBHOOK_SECRET
    original_allowed_hosts = settings.ALLOWED_INTERNAL_WEBHOOK_HOSTS
    settings.INTERNAL_API_CLIENTS = {
        "neectify_shop": InternalApiClientConfig(
            api_key="fake-neectify-shop-key",
            scopes=[
                "customers:create",
                "subscriptions:create",
                "subscriptions:cancel",
                "payments:create",
                "payments:read",
                "jobs:read",
                "metrics:read",
            ],
        )
    }
    settings.ASAAS_WEBHOOK_SECRET = "fake-asaas-webhook-secret-with-32-chars"
    settings.ALLOWED_INTERNAL_WEBHOOK_HOSTS = ["hooks.neectify.local"]

    import arq
    import app.web.main as web_main
    async def fake_create_pool(*args, **kwargs):
        return FakeRedis()
    monkeypatch.setattr(arq, "create_pool", fake_create_pool)
    monkeypatch.setattr(web_main, "create_pool", fake_create_pool)

    yield
    settings.INTERNAL_API_CLIENTS = original
    settings.ASAAS_WEBHOOK_SECRET = original_webhook_secret
    settings.ALLOWED_INTERNAL_WEBHOOK_HOSTS = original_allowed_hosts


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def client(fake_redis, monkeypatch):
    import arq
    import app.web.main as web_main
    async def fake_create_pool(*args, **kwargs):
        return fake_redis
    monkeypatch.setattr(arq, "create_pool", fake_create_pool)
    monkeypatch.setattr(web_main, "create_pool", fake_create_pool)

    with TestClient(app) as test_client:
        app.state.redis_pool = fake_redis
        yield test_client
        app.dependency_overrides.clear()
