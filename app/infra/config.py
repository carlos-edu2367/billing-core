from functools import cached_property

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InternalApiClientConfig(BaseModel):
    api_key: str
    scopes: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "billing-core"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    SQL_ECHO: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT_SECONDS: int = 30
    DB_POOL_RECYCLE_SECONDS: int = 1800
    DB_STATEMENT_TIMEOUT_MS: int = 15000
    INTERNAL_WEBHOOK_SIGNATURE: str
    ASAAS_API_TOKEN: str
    ASAAS_WEBHOOK_SECRET: str
    ASAAS_BASE_URL: str | None = None
    ASAAS_SANDBOX: bool = True
    INTERNAL_API_CLIENTS: dict[str, InternalApiClientConfig] = Field(default_factory=dict)
    CORS_ALLOW_ORIGINS: list[str] = Field(default_factory=list)
    ALLOWED_INTERNAL_WEBHOOK_HOSTS: list[str] = Field(default_factory=list)
    ALLOWED_CHECKOUT_REDIRECT_HOSTS: list[str] = Field(default_factory=list)
    TRUST_PROXY_HEADERS: bool = False
    INTERNAL_RATE_LIMIT_REQUESTS: int = 60
    INTERNAL_RATE_LIMIT_WINDOW_SECONDS: int = 60
    WEBHOOK_RATE_LIMIT_REQUESTS: int = 120
    WEBHOOK_RATE_LIMIT_WINDOW_SECONDS: int = 60
    WEBHOOK_REPLAY_TTL_SECONDS: int = 300
    WEBHOOK_PROCESSING_LOCK_TTL_SECONDS: int = 300
    JOB_METADATA_TTL_SECONDS: int = 86400
    SUBSCRIPTION_IDEMPOTENCY_TTL_SECONDS: int = 86400
    WORKER_JOB_TIMEOUT_SECONDS: int = 120
    WORKER_KEEP_RESULT_SECONDS: int = 3600
    WORKER_MAX_TRIES: int = 3
    WORKER_RETRY_BACKOFF_SECONDS: int = 30
    WORKER_DEAD_LETTER_TTL_SECONDS: int = 604800
    WORKER_MAX_JOBS: int = 10
    INTERNAL_WEBHOOK_MAX_TRIES: int = 5
    API_MAX_PAGE_SIZE: int = 100
    ENABLE_METRICS_ENDPOINT: bool = True
    ENABLE_API_DOCS: bool = True
    MAX_REQUEST_BODY_BYTES: int = 262144
    MAX_WEBHOOK_BODY_BYTES: int = 262144

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"production", "prod"}

    @cached_property
    def resolved_asaas_base_url(self) -> str:
        if self.ASAAS_BASE_URL:
            return self.ASAAS_BASE_URL.rstrip("/")
        return "https://api-sandbox.asaas.com/v3" if self.ASAAS_SANDBOX else "https://api.asaas.com/v3"

    def validate_runtime(self) -> None:
        resolved_base_url = self.resolved_asaas_base_url.lower()

        if self.is_production and "sandbox" in resolved_base_url:
            raise RuntimeError("Configuracao invalida: ambiente de producao nao pode usar endpoint sandbox do Asaas.")

        if self.is_production and "*" in self.CORS_ALLOW_ORIGINS:
            raise RuntimeError("Configuracao invalida: CORS wildcard nao e permitido em producao.")

        if self.MAX_REQUEST_BODY_BYTES <= 0 or self.MAX_WEBHOOK_BODY_BYTES <= 0:
            raise RuntimeError("Configuracao invalida: limites maximos de payload precisam ser positivos.")

        if self.MAX_WEBHOOK_BODY_BYTES > self.MAX_REQUEST_BODY_BYTES:
            raise RuntimeError("Configuracao invalida: MAX_WEBHOOK_BODY_BYTES nao pode exceder MAX_REQUEST_BODY_BYTES.")

        for secret_name, secret_value in {
            "ASAAS_WEBHOOK_SECRET": self.ASAAS_WEBHOOK_SECRET,
            "INTERNAL_WEBHOOK_SIGNATURE": self.INTERNAL_WEBHOOK_SIGNATURE,
        }.items():
            if len(secret_value.strip()) < 32:
                raise RuntimeError(f"Configuracao invalida: {secret_name} deve ter pelo menos 32 caracteres.")
            if any(ch.isspace() for ch in secret_value):
                raise RuntimeError(f"Configuracao invalida: {secret_name} nao pode conter espacos.")

        if self.is_production and not self.ALLOWED_INTERNAL_WEBHOOK_HOSTS:
            raise RuntimeError("Configuracao invalida: ALLOWED_INTERNAL_WEBHOOK_HOSTS e obrigatorio em producao.")

        if self.is_production and not self.ALLOWED_CHECKOUT_REDIRECT_HOSTS:
            raise RuntimeError("Configuracao invalida: ALLOWED_CHECKOUT_REDIRECT_HOSTS e obrigatorio em producao.")

        if self.is_production and not self.INTERNAL_API_CLIENTS:
            raise RuntimeError("Configuracao invalida: INTERNAL_API_CLIENTS nao pode estar vazio em producao.")

        for system_name, client in self.INTERNAL_API_CLIENTS.items():
            if not client.api_key.strip():
                raise RuntimeError(f"Configuracao invalida: INTERNAL_API_CLIENTS[{system_name}] sem api_key.")


settings = Settings()
