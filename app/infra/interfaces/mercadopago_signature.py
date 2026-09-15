"""Validacao do header x-signature dos webhooks do Mercado Pago.

Manifest documentado: id:[data.id_url];request-id:[x-request-id_header];ts:[ts_header];
data.id vai em minusculas e valores ausentes saem do manifest.
"""
import hashlib
import hmac


def _parse_signature_header(header: str) -> tuple[str | None, str | None]:
    values: dict[str, str] = {}
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        values[key.strip()] = value.strip()
    return values.get("ts"), values.get("v1")


def _build_manifest(*, data_id: str | None, request_id: str | None, ts: str) -> str:
    manifest = ""
    if data_id:
        manifest += f"id:{data_id.lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    return manifest + f"ts:{ts};"


def is_valid_signature(*, secret: str, signature_header: str | None, request_id: str | None, data_id: str | None) -> bool:
    if not secret or not signature_header:
        return False

    ts, received = _parse_signature_header(signature_header)
    if not ts or not received:
        return False

    manifest = _build_manifest(data_id=data_id, request_id=request_id, ts=ts)
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
