"""Locks the internal subscription webhook contract (see INTEGRATION.md §7).

The payload delivered to a product's webhook_link must include `system_sub_id`
so the consumer can resolve the subscription by its own identifier — Neectify
Food rejected webhooks without it, breaking subscription activation.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.dtos.response.webhook import InternalEventType, ProcessWebhookResponse
from app.workers.tasks import _build_internal_delivery


@pytest.mark.asyncio
async def test_subscription_internal_webhook_payload_includes_system_sub_id():
    sub_id = uuid4()
    payment_id = uuid4()
    subscription = SimpleNamespace(
        id=sub_id,
        webhook_link="https://hooks.neectify.local/billing/webhook",
        system_subscription_id="store-abc-mensal",
        expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    payment = SimpleNamespace(paid_date=datetime(2026, 6, 1, tzinfo=timezone.utc))

    sub_repo = AsyncMock()
    sub_repo.get_by_id.return_value = subscription
    payment_repo = AsyncMock()
    payment_repo.get_by_id.return_value = payment

    result = ProcessWebhookResponse(
        event=InternalEventType.PAYMENT_RECEIVED,
        payment_id=payment_id,
        subscription_id=sub_id,
    )

    delivery = await _build_internal_delivery(result, sub_repo, payment_repo)

    assert delivery is not None
    assert delivery.payload["system_sub_id"] == "store-abc-mensal"
    assert delivery.payload["subscription_id"] == str(sub_id)
    assert delivery.payload["event"] == "PAYMENT_RECEIVED"
    assert delivery.payload["subscription_expires_at"] == "2026-07-01"
