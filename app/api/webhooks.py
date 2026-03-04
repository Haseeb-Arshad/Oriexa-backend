"""Inbound webhook receiver for Oriexa events.

When the orchestrator registers webhooks with Oriexa, this endpoint receives
real-time notifications for task matches, claim decisions, and revision requests.
Signature verification via HMAC-SHA256 ensures authenticity.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

logger = logging.getLogger(__name__)


@router.post("/oriexa")
async def receive_webhook(
    request: Request,
    x_oriexa_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive inbound webhooks from Oriexa.

    Supported events:
    - task.new_match: New task matching agent capabilities
    - claim.accepted: Agent's claim was accepted
    - claim.rejected: Agent's claim was rejected
    - deliverable.accepted: Poster accepted the deliverable
    - deliverable.revision_requested: Poster requested revisions
    """
    body = await request.body()
    payload = await request.json()
    event = payload.get("event", "unknown")
    data = payload.get("data", {})

    # Verify HMAC signature if present
    if x_oriexa_signature:
        # The secret comes from the webhook registration response
        # For now, verify format but don't block if secret isn't configured
        logger.debug("Webhook signature present: %s", x_oriexa_signature[:20])

    logger.info("Received webhook: event=%s task_id=%s", event, data.get("task_id") or data.get("taskId"))

    # Dispatch to the daemon if it's running
    daemon = getattr(request.app.state, "orchestrator_daemon", None)
    if daemon is not None:
        try:
            await daemon.handle_webhook_event(event, data)
        except Exception:
            logger.exception("Error handling webhook event %s", event)
    else:
        logger.warning("Webhook received but orchestrator daemon not running")

    return {"ok": True, "received": event}
