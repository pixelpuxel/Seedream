import base64
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Generation, GenerationImage, GenerationReference, GenerationStatus, ProviderRequest, ProviderResponse
from app.services.images import inspect_image, prepare_provider_image
from app.services.apns import send_generation_completed
from app.services.provider import ArkSeedreamProvider, ProviderError, parse_size, safe_provider_payload
from app.services.provider_config import provider_api_key
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, autoretry_for=(), name="seedream.process_generation")
def process_generation(self, generation_id: str) -> None:
    settings = get_settings()
    storage = get_storage()
    with SessionLocal() as db:
        generation = db.scalar(
            select(Generation)
            .where(Generation.id == generation_id)
            .options(selectinload(Generation.references).selectinload(GenerationReference.image))
        )
        if not generation or generation.status not in {GenerationStatus.queued, GenerationStatus.processing}:
            return
        generation.status = GenerationStatus.processing
        generation.started_at = datetime.now(UTC)
        db.commit()
        try:
            references = [prepare_provider_image(storage.read(reference.image.storage_key)) for reference in generation.references]
            api_key, _ = provider_api_key(db, generation.user_id)
            provider = ArkSeedreamProvider(settings, api_key=api_key)
            payload = provider.build_payload(
                model=generation.model,
                prompt=generation.final_prompt,
                resolution=generation.resolution,
                aspect_ratio=generation.aspect_ratio,
                references=references,
                parameters=generation.parameters or {},
            )
            db.add(ProviderRequest(generation_id=generation.id, endpoint=settings.seedream_endpoint, payload=safe_provider_payload(payload)))
            db.commit()
            result = provider.generate(payload)
            db.add(ProviderResponse(generation_id=generation.id, status_code=result.status_code, payload=result.payload))
            for index, item in enumerate(result.payload["data"]):
                data, mime, provider_url = _download_result(item, settings.provider_timeout_seconds)
                info = inspect_image(data, mime)
                key, byte_size = storage.put(data, info.extension)
                response_width, response_height = parse_size(item.get("size"))
                db.add(
                    GenerationImage(
                        user_id=generation.user_id,
                        generation_id=generation.id,
                        storage_key=key,
                        original_filename=f"seedream-{generation.id}-{index + 1}.{info.extension}",
                        mime_type=info.mime_type,
                        width=response_width or info.width,
                        height=response_height or info.height,
                        byte_size=byte_size,
                        role="result" if item.get("z_index", 0) == 0 else "layer",
                        provider_url=provider_url,
                    )
                )
            generation.status = GenerationStatus.completed
            generation.completed_at = datetime.now(UTC)
            db.commit()
            try:
                send_generation_completed(db, generation.id)
            except Exception:
                db.rollback()
                logger.exception("Push notification failed for completed generation %s", generation.id)
        except ProviderError as exc:
            db.rollback()
            generation = db.get(Generation, generation_id)
            if generation:
                generation.status = GenerationStatus.failed
                generation.error_code = exc.code
                generation.error_message = exc.user_message
                generation.completed_at = datetime.now(UTC)
                existing = db.scalar(select(ProviderResponse).where(ProviderResponse.generation_id == generation_id))
                if not existing:
                    db.add(ProviderResponse(generation_id=generation_id, status_code=exc.status_code, payload=exc.payload))
                db.commit()
            logger.exception("Seedream provider failure for generation %s", generation_id)
        except Exception:
            db.rollback()
            generation = db.get(Generation, generation_id)
            if generation:
                generation.status = GenerationStatus.failed
                generation.error_code = "internal_processing_error"
                generation.error_message = "Das Ergebnis konnte nicht verarbeitet oder gespeichert werden."
                generation.completed_at = datetime.now(UTC)
                db.commit()
            logger.exception("Unhandled generation failure %s", generation_id)
            raise


def _download_result(item: dict, timeout: int) -> tuple[bytes, str, str | None]:
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"], validate=True)
        mime = "image/png" if item.get("output_format") == "png" else "image/jpeg"
        return data, mime, None
    url = item.get("url")
    if not isinstance(url, str) or urlparse(url).scheme != "https":
        raise ProviderError("invalid_result_url", "Seedream hat eine ungültige Ergebnisadresse geliefert.", 502, item)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        if len(response.content) > 50 * 1024 * 1024:
            raise ProviderError("result_too_large", "Das erzeugte Bild überschreitet die Speichergrenze.", 502)
        return response.content, response.headers.get("content-type", "image/jpeg").split(";")[0], url
