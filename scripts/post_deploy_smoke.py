from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def request_json(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> tuple[int, str]:
    payload = None
    request_headers = headers.copy() if headers else {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(
        url=url,
        method=method,
        headers=request_headers,
        data=payload,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa smoke tests basicos apos deploy do Billing Core."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="URL base do servico, ex: https://billing.example.com",
    )
    parser.add_argument(
        "--system",
        help="Valor do header X-System para smoke test autenticado",
    )
    parser.add_argument(
        "--api-key",
        help="Valor do header X-API-Key para smoke test autenticado",
    )
    parser.add_argument(
        "--skip-authenticated",
        action="store_true",
        help="Pula o smoke test autenticado de subscriptions.",
    )
    args = parser.parse_args()

    success = True
    unauthenticated_endpoints = ["/health", "/ready", "/live"]

    for path in unauthenticated_endpoints:
        status, body = request_json(f"{args.base_url.rstrip('/')}{path}")
        if status != 200:
            print(f"[FAIL] {path} retornou {status}")
            print(body)
            success = False
        else:
            print(f"[OK] {path} retornou 200")

    if not args.skip_authenticated:
        if not args.system or not args.api_key:
            print("[FAIL] Informe --system e --api-key ou use --skip-authenticated")
            return 1

        headers = {
            "X-System": args.system,
            "X-API-Key": args.api_key,
            "Idempotency-Key": "smoke-test-key",
        }
        metrics_status, metrics_body = request_json(
            f"{args.base_url.rstrip('/')}/metrics",
            headers={"X-System": args.system, "X-API-Key": args.api_key},
        )
        if metrics_status != 200:
            print(f"[FAIL] /metrics retornou {metrics_status}")
            print(metrics_body)
            success = False
        else:
            print("[OK] /metrics retornou 200")

        payload = {
            "customer_provider_id": "smoke_customer",
            "value": "19.90",
            "subscription_type": "MONTHLY",
            "next_due_date": "2026-05-01",
            "description": "Smoke test de deploy",
            "system": args.system,
            "system_sub_id": "smoke-subscription",
            "expires_at": "2027-05-01T00:00:00Z",
            "webhook_link": "https://hooks.neectify.local/billing/subscription",
        }
        status, body = request_json(
            f"{args.base_url.rstrip('/')}/v1/subscriptions",
            method="POST",
            headers=headers,
            body=payload,
        )
        if status not in {200, 202, 409, 422}:
            print(f"[FAIL] /v1/subscriptions retornou status inesperado: {status}")
            print(body)
            success = False
        else:
            print(f"[OK] /v1/subscriptions respondeu com status esperado: {status}")
            if status == 202:
                try:
                    response_json = json.loads(body)
                    job_id = response_json.get("job_id")
                except json.JSONDecodeError:
                    job_id = None

                if job_id:
                    job_status, job_body = request_json(
                        f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}",
                        headers={"X-System": args.system, "X-API-Key": args.api_key},
                    )
                    if job_status != 200:
                        print(f"[FAIL] /v1/jobs/{job_id} retornou {job_status}")
                        print(job_body)
                        success = False
                    else:
                        print(f"[OK] /v1/jobs/{job_id} retornou 200")
    else:
        metrics_status, metrics_body = request_json(f"{args.base_url.rstrip('/')}/metrics")
        if metrics_status != 401 and metrics_status != 422:
            print(f"[FAIL] /metrics sem autenticacao retornou status inesperado: {metrics_status}")
            print(metrics_body)
            success = False
        else:
            print(f"[OK] /metrics protegido por autenticacao: {metrics_status}")

    if success:
        print("[OK] Smoke tests pos-deploy aprovados")
        return 0

    print("[FAIL] Smoke tests pos-deploy reprovados")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
