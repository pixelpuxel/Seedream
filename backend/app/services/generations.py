import json
from collections.abc import Sequence

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import (
    Generation,
    GenerationImage,
    GenerationKind,
    GenerationPromptTemplate,
    GenerationReference,
    PromptTemplate,
    User,
)
from app.services.images import inspect_image
from app.services.storage import get_storage


LEGACY_MODEL_ROLES = {
    "doubao-seedream-5-0-pro-260628": "pro",
    "doubao-seedream-5-0-flash-260915": "flash",
    "doubao-seedream-5-0-260128": "lite",
}


def normalize_model(model: str | None, settings) -> str:
    chosen = model or settings.seedream_model
    role = LEGACY_MODEL_ROLES.get(chosen)
    if role == "pro":
        return settings.seedream_model
    if role == "flash":
        return settings.seedream_flash_model
    if role == "lite":
        return settings.seedream_lite_model
    return chosen


def parse_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, detail={"code": "invalid_list", "message": "Listen müssen als JSON-Array übergeben werden."}) from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise HTTPException(422, detail={"code": "invalid_list", "message": "Ungültige Liste."})
    return parsed


def parse_parameters(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, detail={"code": "invalid_parameters", "message": "Parameter müssen ein JSON-Objekt sein."}) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(422, detail={"code": "invalid_parameters", "message": "Parameter müssen ein JSON-Objekt sein."})
    return parsed


async def create_generation(
    db: Session,
    user: User,
    *,
    prompt: str,
    final_prompt: str | None,
    template_ids: Sequence[str],
    image_ids: Sequence[str],
    uploads: Sequence[UploadFile],
    model: str | None,
    aspect_ratio: str,
    resolution: str | None,
    parent_id: str | None,
    kind: GenerationKind,
    parameters: dict,
) -> Generation:
    settings = get_settings()
    allowed_models = {settings.seedream_model, settings.seedream_flash_model, settings.seedream_lite_model}
    chosen_model = normalize_model(model, settings)
    if chosen_model not in allowed_models:
        raise HTTPException(422, detail={"code": "unsupported_model", "message": "Das ausgewählte Modell ist nicht konfiguriert."})
    chosen_resolution = resolution or settings.seedream_default_size
    if chosen_resolution not in settings.configured_sizes and not _valid_pixel_size(chosen_resolution):
        raise HTTPException(422, detail={"code": "unsupported_resolution", "message": "Die ausgewählte Auflösung ist nicht konfiguriert."})
    if aspect_ratio not in {"auto", "1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"}:
        raise HTTPException(422, detail={"code": "unsupported_aspect_ratio", "message": "Nicht unterstütztes Seitenverhältnis."})
    parent = None
    if parent_id:
        parent = db.scalar(select(Generation).where(Generation.id == parent_id, Generation.user_id == user.id))
        if not parent:
            raise HTTPException(404, detail={"code": "parent_not_found", "message": "Ausgangsgeneration nicht gefunden."})

    templates = []
    for template_id in template_ids:
        template = db.scalar(select(PromptTemplate).where(PromptTemplate.id == template_id, PromptTemplate.user_id == user.id))
        if not template:
            raise HTTPException(404, detail={"code": "template_not_found", "message": "Ein Prompt-Baustein wurde nicht gefunden."})
        templates.append(template)
    composed = "\n\n".join([template.text.strip() for template in templates] + ([prompt.strip()] if prompt.strip() else []))
    effective_prompt = (final_prompt or composed).strip()
    if not effective_prompt:
        raise HTTPException(422, detail={"code": "prompt_required", "message": "Bitte einen Prompt eingeben oder Prompt-Bausteine auswählen."})

    generation = Generation(
        user_id=user.id,
        parent_id=parent.id if parent else None,
        kind=kind,
        prompt=prompt.strip(),
        final_prompt=effective_prompt,
        model=chosen_model,
        aspect_ratio=aspect_ratio,
        resolution=chosen_resolution,
        parameters=parameters,
    )
    db.add(generation)
    db.flush()
    for position, template in enumerate(templates):
        db.add(
            GenerationPromptTemplate(
                generation_id=generation.id,
                template_id=template.id,
                position=position,
                name_snapshot=template.name,
                text_snapshot=template.text,
            )
        )

    reference_images: list[tuple[GenerationImage, str]] = []
    for image_id in image_ids:
        image = db.scalar(select(GenerationImage).where(GenerationImage.id == image_id, GenerationImage.user_id == user.id))
        if not image:
            raise HTTPException(404, detail={"code": "image_not_found", "message": "Ein Referenzbild wurde nicht gefunden."})
        reference_images.append((image, "reference"))
    for upload in uploads:
        data = await upload.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, detail={"code": "file_too_large", "message": "Ein Bild überschreitet die Upload-Grenze."})
        try:
            info = inspect_image(data, upload.content_type)
        except ValueError as exc:
            raise HTTPException(415, detail={"code": "invalid_image", "message": str(exc)}) from exc
        key, size = get_storage().put(data, info.extension)
        image = GenerationImage(
            user_id=user.id,
            generation_id=generation.id,
            storage_key=key,
            original_filename=(upload.filename or "upload")[:255],
            mime_type=info.mime_type,
            width=info.width,
            height=info.height,
            byte_size=size,
            role="reference",
        )
        db.add(image)
        db.flush()
        reference_images.append((image, "reference"))
    max_references = 14 if chosen_model == settings.seedream_lite_model else 10
    if len(reference_images) > max_references:
        raise HTTPException(422, detail={"code": "too_many_references", "message": f"Dieses Modell unterstützt höchstens {max_references} Referenzbilder."})
    for position, (image, purpose) in enumerate(reference_images):
        db.add(GenerationReference(generation_id=generation.id, image_id=image.id, position=position, purpose=purpose))
    db.commit()
    return load_generation(db, generation.id, user.id)


def clone_generation(db: Session, user: User, source: Generation, kind: GenerationKind) -> Generation:
    settings = get_settings()
    clone = Generation(
        user_id=user.id,
        parent_id=source.id,
        kind=kind,
        prompt=source.prompt,
        final_prompt=source.final_prompt,
        model=normalize_model(source.model, settings),
        aspect_ratio=source.aspect_ratio,
        resolution=source.resolution,
        seed=source.seed,
        parameters=dict(source.parameters or {}),
    )
    db.add(clone)
    db.flush()
    for link in source.template_links:
        db.add(GenerationPromptTemplate(generation_id=clone.id, template_id=link.template_id, position=link.position, name_snapshot=link.name_snapshot, text_snapshot=link.text_snapshot))
    references = [(reference.image_id, reference.purpose) for reference in source.references]
    if kind == GenerationKind.variation:
        result = next((image for image in source.images if image.role == "result"), None)
        if result:
            max_references = 14 if clone.model == settings.seedream_lite_model else 10
            references = [(result.id, "variation_source")] + [item for item in references if item[0] != result.id]
            references = references[:max_references]
    for position, (image_id, purpose) in enumerate(references):
        db.add(GenerationReference(generation_id=clone.id, image_id=image_id, position=position, purpose=purpose))
    db.commit()
    return load_generation(db, clone.id, user.id)


def load_generation(db: Session, generation_id: str, user_id: str) -> Generation:
    generation = db.scalar(
        select(Generation)
        .where(Generation.id == generation_id, Generation.user_id == user_id)
        .options(
            selectinload(Generation.images),
            selectinload(Generation.references).selectinload(GenerationReference.image),
            selectinload(Generation.template_links),
        )
    )
    if not generation:
        raise HTTPException(404, detail={"code": "generation_not_found", "message": "Generation nicht gefunden."})
    return generation


def _valid_pixel_size(value: str) -> bool:
    try:
        width, height = [int(part) for part in value.lower().split("x", 1)]
    except (ValueError, TypeError):
        return False
    pixels = width * height
    return 921_600 <= pixels <= 4_624_220 and 1 / 16 <= width / height <= 16
