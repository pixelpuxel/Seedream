import base64
import logging
import time
from datetime import UTC, datetime

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.security import create_download_token
from app.models import DeviceRegistration, Generation, GenerationImage

logger = logging.getLogger(__name__)


def send_generation_completed(db: Session, generation_id: str) -> None:
    settings = get_settings()
    if not settings.apns_configured:
        return
    generation = db.scalar(
        select(Generation).where(Generation.id == generation_id).options(selectinload(Generation.images))
    )
    if not generation:
        return
    result = next((image for image in generation.images if image.role == "result"), None)
    if not result:
        return
    devices = db.scalars(
        select(DeviceRegistration).where(
            DeviceRegistration.user_id == generation.user_id,
            DeviceRegistration.is_active.is_(True),
        )
    ).all()
    if not devices:
        return

    token = _provider_token(settings.apns_private_key_b64, settings.apns_key_id, settings.apns_team_id)
    media_token = create_download_token(result.id, expires_minutes=24 * 60)
    media_url = f"{settings.public_base_url.rstrip('/')}/api/v1/images/{result.id}/content?token={media_token}"
    prompt = " ".join(generation.final_prompt.split())
    body = prompt[:137] + "…" if len(prompt) > 138 else prompt
    payload = {
        "aps": {
            "alert": {"title": "Dein Bild ist fertig", "body": body or "Tippe, um das Ergebnis zu öffnen."},
            "sound": "default",
            "mutable-content": 1,
        },
        "generation_id": generation.id,
        "media_url": media_url,
        "deep_link": f"seedream://generation/{generation.id}",
    }
    with httpx.Client(http2=True, timeout=15) as client:
        for device in devices:
            host = "https://api.sandbox.push.apple.com" if device.environment == "sandbox" else "https://api.push.apple.com"
            response = client.post(
                f"{host}/3/device/{device.device_token}",
                headers={
                    "authorization": f"bearer {token}",
                    "apns-topic": device.bundle_id,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
                json=payload,
            )
            if response.status_code == 200:
                continue
            reason = _reason(response)
            logger.warning("APNs rejected notification status=%s reason=%s device_id=%s", response.status_code, reason, device.id)
            if response.status_code in {400, 410} and reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}:
                device.is_active = False
                device.updated_at = datetime.now(UTC)
    db.commit()


def _provider_token(encoded_key: str, key_id: str, team_id: str) -> str:
    private_key = base64.b64decode(encoded_key, validate=True).decode("utf-8")
    return jwt.encode(
        {"iss": team_id, "iat": int(time.time())},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )


def _reason(response: httpx.Response) -> str:
    try:
        return str(response.json().get("reason", "Unknown"))
    except (ValueError, AttributeError):
        return "Unknown"
