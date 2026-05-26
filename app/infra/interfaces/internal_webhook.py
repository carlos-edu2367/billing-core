import base64
import dataclasses
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse

import httpx

from app.application.interfaces.internal_webhook import InternalWebhook
from app.infra.config import settings

logger = logging.getLogger(__name__)


def _safe_destination_label(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or parsed.path or "unknown"


class InternalWebhookProvider(InternalWebhook):
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.signature_secret = settings.INTERNAL_WEBHOOK_SIGNATURE

    async def send(self, url: str, payload, webhook_id: str | None = None, event_type: str | None = None) -> dict | None:
        if hasattr(payload, "model_dump"):
            payload_dict = payload.model_dump(mode="json")
        elif dataclasses.is_dataclass(payload):
            payload_dict = dataclasses.asdict(payload)
        else:
            payload_dict = payload

        payload_json = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            self.signature_secret.encode("utf-8"),
            payload_json.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        signature_b64 = base64.b64encode(signature).decode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature-256": signature_b64,
        }
        if webhook_id:
            headers["X-Webhook-Id"] = webhook_id
        if event_type:
            headers["X-Webhook-Event"] = event_type

        destination = _safe_destination_label(url)
        try:
            resp = await self.client.post(url, json=payload_dict, headers=headers)
            resp.raise_for_status()
            logger.info(
                "Internal webhook sent",
                extra={"destination": destination, "status_code": resp.status_code},
            )
            return resp.json() if resp.content else None
        except Exception as exc:
            logger.error(
                "Internal webhook failed",
                extra={"destination": destination, "error": str(exc)},
            )
            raise
