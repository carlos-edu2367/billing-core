from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED_DOCS = [
    "README.md",
    "docs/Arquitetura.md",
    "docs/API.md",
    "docs/Webhooks.md",
    "docs/Ambiente.md",
    "docs/Fluxos.md",
    "docs/Onboarding_SaaS.md",
    "docs/Checklist_Final_Producao.md",
    "runbooks/Deploy_Rollback.md",
    "runbooks/Incidente_Operacional.md",
    "runbooks/Falha_Gateway.md",
    "runbooks/Reconciliacao_Financeira.md",
]

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "REDIS_URL",
    "ASAAS_API_TOKEN",
    "ASAAS_WEBHOOK_SECRET",
    "INTERNAL_WEBHOOK_SIGNATURE",
    "INTERNAL_API_CLIENTS",
]

REQUIRED_ROUTES = {
    "/health",
    "/ready",
    "/live",
    "/metrics",
    "/v1/subscriptions",
    "/v1/jobs/{job_id}",
    "/v1/webhooks/asaas",
}

PLACEHOLDER_MARKERS = ("changeme", "replace-me", "example", "fake", "todo", "test")


def ok(message: str) -> None:
    print(f"[OK] {message}")


def fail(message: str) -> None:
    print(f"[FAIL] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def run_command(command: list[str], label: str) -> bool:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode == 0:
        ok(label)
        if result.stdout.strip():
            print(result.stdout.strip())
        return True

    fail(label)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    return False


def check_docs() -> bool:
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).exists()]
    if missing:
        fail("Documentacao operacional obrigatoria ausente")
        for path in missing:
            print(f"  - {path}")
        return False

    ok("Documentacao obrigatoria presente")
    return True


def check_env() -> bool:
    success = True
    app_env = os.getenv("APP_ENV", "development").strip().lower()

    for key in REQUIRED_ENV_VARS:
        value = os.getenv(key)
        if not value:
            fail(f"Variavel obrigatoria ausente: {key}")
            success = False
            continue

        lowered = value.lower()
        if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            if app_env in {"production", "prod"}:
                fail(f"Variavel com valor de placeholder em producao: {key}")
                success = False
            else:
                warn(f"Variavel com valor de placeholder em ambiente nao produtivo: {key}")
            continue

        ok(f"Variavel presente: {key}")

    asaas_base_url = os.getenv("ASAAS_BASE_URL", "").strip().lower()
    asaas_sandbox = os.getenv("ASAAS_SANDBOX", "true").strip().lower() == "true"
    if app_env in {"production", "prod"}:
        if "sandbox" in asaas_base_url or (not asaas_base_url and asaas_sandbox):
            fail("Configuracao invalida: ambiente de producao apontando para sandbox do Asaas")
            success = False

    return success


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def check_application_routes() -> bool:
    try:
        from app.web.main import app
    except Exception as exc:
        fail(f"Aplicacao FastAPI nao inicializou: {exc}")
        return False

    registered_routes = {route.path for route in app.routes}
    missing = sorted(REQUIRED_ROUTES - registered_routes)
    if missing:
        fail("Rotas obrigatorias ausentes")
        for route in missing:
            print(f"  - {route}")
        return False

    ok("Rotas operacionais e criticas registradas")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o preflight final de producao do Billing Core."
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Executa a suite pytest como parte do checklist.",
    )
    parser.add_argument(
        "--check-migrations",
        action="store_true",
        help="Valida se o banco local/ambiente esta alinhado ao head do Alembic.",
    )
    args = parser.parse_args()
    load_dotenv()

    checks = [
        check_docs(),
        check_env(),
        check_application_routes(),
    ]

    if args.run_tests:
        checks.append(run_command([sys.executable, "-m", "pytest", "-q"], "Suite de testes executada"))

    if args.check_migrations:
        checks.append(run_command([sys.executable, "-m", "alembic", "heads"], "Alembic heads acessivel"))
        checks.append(run_command([sys.executable, "-m", "alembic", "current"], "Alembic current acessivel"))

    if all(checks):
        ok("Checklist de preflight aprovado")
        return 0

    fail("Checklist de preflight reprovado")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
