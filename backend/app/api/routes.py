import asyncio
import json
from datetime import UTC, datetime
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import get_current_user, require_admin
from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.security import create_access_token, create_api_key, create_session_token, hash_password, hash_session_token, verify_download_token, verify_password
from app.database import SessionLocal, get_db
from app.models import APIKey, DeviceRegistration, Generation, GenerationImage, GenerationKind, GenerationReference, GenerationStatus, PromptTemplate, PromptTemplateTag, ProviderRequest, ProviderResponse, ReferenceAsset, Setting, SetupState, User, UserSession
from app.schemas import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyResponse,
    DeviceRegistrationInput,
    DeviceRegistrationResponse,
    GenerationList,
    GenerationResponse,
    LoginRequest,
    RefreshRequest,
    SetupRequest,
    UserInput,
    ModelCapability,
    ModelsResponse,
    PromptTemplateInput,
    PromptTemplateResponse,
    ReferenceAssetResponse,
    ProviderConfigurationInput,
    ProviderConfigurationResponse,
    SettingsResponse,
    TokenResponse,
    UserResponse,
)
from app.services.generations import clone_generation, create_generation, load_generation, parse_parameters, parse_string_list
from app.services.images import inspect_image, make_thumbnail
from app.services.provider_config import provider_api_key, provider_status, remove_provider_api_key, save_provider_api_key
from app.services.serializers import generation_dict, image_dict, prompt_dict, reference_asset_dict
from app.services.storage import get_storage

router = APIRouter()


def _issue_session(db: Session, user: User) -> dict:
    raw, hashed = create_session_token()
    db.add(UserSession(user_id=user.id, token_hash=hashed))
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return {"access_token": create_access_token(user.id), "session_token": raw, "user": user}


@router.get("/setup/status", tags=["Ersteinrichtung"])
def setup_status(db: Session = Depends(get_db)):
    completed = db.scalar(select(SetupState).where(SetupState.id == "singleton"))
    return {"setup_required": not bool(completed and completed.completed_at)}


@router.post("/setup/initialize", response_model=TokenResponse, tags=["Ersteinrichtung"])
def initialize_setup(payload: SetupRequest, x_setup_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    settings = get_settings()
    if not x_setup_token or not hmac.compare_digest(x_setup_token, settings.setup_token):
        raise HTTPException(403, detail={"code": "invalid_setup_token", "message": "Der Einrichtungsschlüssel ist ungültig."})
    state = db.get(SetupState, "singleton")
    if (state and state.completed_at) or (db.scalar(select(func.count(User.id))) or 0) > 0:
        raise HTTPException(409, detail={"code": "setup_completed", "message": "Die Ersteinrichtung ist bereits abgeschlossen."})
    user = User(username=payload.username.lower().strip(), email=payload.email.lower().strip() if payload.email else None, display_name=payload.display_name.strip(), password_hash=hash_password(payload.password), is_active=True, is_admin=True)
    db.add(user)
    db.flush()
    db.add(Setting(user_id=user.id, data={"auto_save_photos": False, "auto_save_album": False, "album_name": "Seedream"}))
    db.add(SetupState(id="singleton", completed_at=datetime.now(UTC)))
    return _issue_session(db, user)


@router.post("/auth/login", response_model=TokenResponse, tags=["Authentifizierung"])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.username) == payload.username.lower().strip()))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, detail={"code": "invalid_credentials", "message": "Benutzername oder Passwort ist falsch."})
    return _issue_session(db, user)


@router.post("/auth/refresh", response_model=TokenResponse, tags=["Authentifizierung"])
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_session_token(payload.session_token), UserSession.revoked_at.is_(None)))
    user = db.get(User, session.user_id) if session else None
    if not session or not user or not user.is_active:
        raise HTTPException(401, detail={"code": "invalid_session", "message": "Die Sitzung ist abgelaufen oder wurde widerrufen."})
    session.last_used_at = datetime.now(UTC)
    db.commit()
    return {"access_token": create_access_token(user.id), "session_token": payload.session_token, "user": user}


@router.post("/auth/logout", status_code=204, tags=["Authentifizierung"])
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_session_token(payload.session_token)))
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.commit()


@router.post("/devices", response_model=DeviceRegistrationResponse, tags=["Push-Nachrichten"])
def register_device(payload: DeviceRegistrationInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = payload.device_token.lower()
    device = db.scalar(select(DeviceRegistration).where(DeviceRegistration.device_token == token))
    if device is None:
        device = DeviceRegistration(user_id=user.id, device_token=token)
    device.user_id = user.id
    device.environment = payload.environment
    device.bundle_id = payload.bundle_id
    device.is_active = True
    device.updated_at = datetime.now(UTC)
    db.add(device)
    db.commit()
    return {"registered": True}


@router.delete("/devices/current", response_model=DeviceRegistrationResponse, tags=["Push-Nachrichten"])
def unregister_device(payload: DeviceRegistrationInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    device = db.scalar(
        select(DeviceRegistration).where(
            DeviceRegistration.device_token == payload.device_token.lower(),
            DeviceRegistration.user_id == user.id,
        )
    )
    if device:
        device.is_active = False
        device.updated_at = datetime.now(UTC)
        db.commit()
    return {"registered": False}


@router.get("/users", response_model=list[UserResponse], tags=["Benutzer"])
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.created_at)).all()


@router.post("/users", response_model=UserResponse, status_code=201, tags=["Benutzer"])
def create_user(payload: UserInput, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    username = payload.username.lower().strip()
    email = payload.email.lower().strip() if payload.email else None
    if db.scalar(select(User).where(func.lower(User.username) == username)):
        raise HTTPException(409, detail={"code": "username_exists", "message": "Dieser Benutzername ist bereits vergeben."})
    if email and db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, detail={"code": "email_exists", "message": "Diese E-Mail-Adresse ist bereits vergeben."})
    user = User(username=username, email=email, display_name=payload.display_name.strip(), password_hash=hash_password(payload.password), is_active=True, is_admin=payload.is_admin)
    db.add(user)
    db.flush()
    db.add(Setting(user_id=user.id, data={"auto_save_photos": False, "auto_save_album": False, "album_name": "Seedream"}))
    db.commit()
    db.refresh(user)
    return user


@router.get("/auth/me", response_model=UserResponse, tags=["Authentifizierung"])
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/generate", response_model=GenerationResponse, status_code=202, tags=["Generierung"])
async def generate(
    prompt: Annotated[str, Form()] = "",
    final_prompt: Annotated[str | None, Form()] = None,
    template_ids: Annotated[str | None, Form()] = None,
    image_ids: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    aspect_ratio: Annotated[str, Form()] = "auto",
    resolution: Annotated[str | None, Form()] = None,
    parent_id: Annotated[str | None, Form()] = None,
    parameters: Annotated[str | None, Form()] = None,
    reference_images: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not provider_api_key(db, user.id)[0]:
        raise HTTPException(503, detail={"code": "provider_not_configured", "message": "Seedream ist noch nicht eingerichtet. Bitte den API-Key unter Einstellungen hinterlegen."})
    generation = await create_generation(
        db, user, prompt=prompt, final_prompt=final_prompt, template_ids=parse_string_list(template_ids),
        image_ids=parse_string_list(image_ids), uploads=reference_images, model=model, aspect_ratio=aspect_ratio,
        resolution=resolution, parent_id=parent_id, kind=GenerationKind.generate, parameters=parse_parameters(parameters),
    )
    _enqueue(generation.id)
    return generation_dict(generation)


@router.post("/edit", response_model=GenerationResponse, status_code=202, tags=["Generierung"])
async def edit(
    parent_id: Annotated[str, Form()],
    prompt: Annotated[str, Form()] = "",
    final_prompt: Annotated[str | None, Form()] = None,
    template_ids: Annotated[str | None, Form()] = None,
    image_ids: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    aspect_ratio: Annotated[str, Form()] = "auto",
    resolution: Annotated[str | None, Form()] = None,
    parameters: Annotated[str | None, Form()] = None,
    reference_images: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not provider_api_key(db, user.id)[0]:
        raise HTTPException(503, detail={"code": "provider_not_configured", "message": "Seedream ist noch nicht eingerichtet. Bitte den API-Key unter Einstellungen hinterlegen."})
    generation = await create_generation(
        db, user, prompt=prompt, final_prompt=final_prompt, template_ids=parse_string_list(template_ids),
        image_ids=parse_string_list(image_ids), uploads=reference_images, model=model, aspect_ratio=aspect_ratio,
        resolution=resolution, parent_id=parent_id, kind=GenerationKind.edit, parameters=parse_parameters(parameters),
    )
    _enqueue(generation.id)
    return generation_dict(generation)


@router.get("/generations", response_model=GenerationList, tags=["Generierungen"])
def list_generations(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status_filter: GenerationStatus | None = Query(None, alias="status"),
    parent_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base = select(Generation).where(Generation.user_id == user.id)
    count_query = select(func.count(Generation.id)).where(Generation.user_id == user.id)
    if status_filter:
        base, count_query = base.where(Generation.status == status_filter), count_query.where(Generation.status == status_filter)
    if parent_id:
        base, count_query = base.where(Generation.parent_id == parent_id), count_query.where(Generation.parent_id == parent_id)
    items = db.scalars(base.order_by(Generation.created_at.desc()).offset(offset).limit(limit)).all()
    hydrated = [load_generation(db, item.id, user.id) for item in items]
    return {"items": [generation_dict(item) for item in hydrated], "total": db.scalar(count_query) or 0, "limit": limit, "offset": offset}


@router.get("/generations/{generation_id}", response_model=GenerationResponse, tags=["Generierungen"])
def get_generation(generation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return generation_dict(load_generation(db, generation_id, user.id))


@router.delete("/generations/{generation_id}", tags=["Generierungen"])
def delete_generation(generation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    generation = load_generation(db, generation_id, user.id)
    if generation.status in {GenerationStatus.queued, GenerationStatus.processing}:
        raise HTTPException(409, detail={"code": "generation_active", "message": "Ein laufender Auftrag kann noch nicht gelöscht werden."})
    storage_keys: list[str] = []
    shared_image_ids: list[str] = []
    for image in generation.images:
        used_elsewhere = db.scalar(
            select(func.count(GenerationReference.id)).where(
                GenerationReference.image_id == image.id,
                GenerationReference.generation_id != generation.id,
            )
        ) or db.scalar(select(func.count(ReferenceAsset.id)).where(ReferenceAsset.image_id == image.id))
        if used_elsewhere:
            shared_image_ids.append(image.id)
        else:
            storage_keys.append(image.storage_key)
    if shared_image_ids:
        db.execute(update(GenerationImage).where(GenerationImage.id.in_(shared_image_ids)).values(generation_id=None))
    db.query(ProviderRequest).filter(ProviderRequest.generation_id == generation.id).delete(synchronize_session=False)
    db.query(ProviderResponse).filter(ProviderResponse.generation_id == generation.id).delete(synchronize_session=False)
    db.execute(delete(Generation).where(Generation.id == generation.id))
    db.commit()
    storage = get_storage()
    for key in storage_keys:
        storage.delete(key)
    return {"deleted": True, "files_deleted": len(storage_keys)}


@router.post("/generations/{generation_id}/regenerate", response_model=GenerationResponse, status_code=202, tags=["Generierungen"])
def regenerate(generation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not provider_api_key(db, user.id)[0]:
        raise HTTPException(503, detail={"code": "provider_not_configured", "message": "Seedream ist noch nicht eingerichtet. Bitte den API-Key unter Einstellungen hinterlegen."})
    source = load_generation(db, generation_id, user.id)
    clone = clone_generation(db, user, source, GenerationKind.regenerate)
    _enqueue(clone.id)
    return generation_dict(clone)


@router.post("/generations/{generation_id}/variation", response_model=GenerationResponse, status_code=202, tags=["Generierungen"])
def variation(generation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not provider_api_key(db, user.id)[0]:
        raise HTTPException(503, detail={"code": "provider_not_configured", "message": "Seedream ist noch nicht eingerichtet. Bitte den API-Key unter Einstellungen hinterlegen."})
    source = load_generation(db, generation_id, user.id)
    clone = clone_generation(db, user, source, GenerationKind.variation)
    clone.parameters = {**(clone.parameters or {}), "variation": True}
    db.commit()
    _enqueue(clone.id)
    return generation_dict(load_generation(db, clone.id, user.id))


@router.get("/generations/{generation_id}/events", tags=["Generierungen"])
def generation_events(generation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    load_generation(db, generation_id, user.id)

    async def stream():
        last = None
        for _ in range(360):
            with SessionLocal() as event_db:
                generation = event_db.get(Generation, generation_id)
                if not generation or generation.user_id != user.id:
                    yield "event: error\ndata: {\"message\":\"Nicht gefunden\"}\n\n"
                    return
                data = json.dumps({"id": generation.id, "status": generation.status.value, "error_message": generation.error_message})
                if data != last:
                    yield f"event: status\ndata: {data}\n\n"
                    last = data
                if generation.status in {GenerationStatus.completed, GenerationStatus.failed}:
                    return
            await asyncio.sleep(1)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/images", status_code=201, tags=["Bilder"])
async def upload_image(
    image: UploadFile = File(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    data = await image.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, detail={"code": "file_too_large", "message": "Das Bild überschreitet die Upload-Grenze."})
    try:
        info = inspect_image(data, image.content_type)
    except ValueError as exc:
        raise HTTPException(415, detail={"code": "invalid_image", "message": str(exc)}) from exc
    key, size = get_storage().put(data, info.extension)
    record = GenerationImage(user_id=user.id, generation_id=None, storage_key=key, original_filename=(image.filename or "upload")[:255], mime_type=info.mime_type, width=info.width, height=info.height, byte_size=size, role="upload")
    db.add(record)
    db.commit()
    db.refresh(record)
    return image_dict(record)


@router.get("/images", tags=["Bilder"])
def list_images(limit: int = Query(50, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(GenerationImage).where(GenerationImage.user_id == user.id).order_by(GenerationImage.created_at.desc()).limit(limit)).all()
    return [image_dict(item) for item in items]


@router.get("/images/{image_id}/content", tags=["Bilder"])
def image_content(image_id: str, token: str = Query(...), thumbnail: int | None = Query(default=None, ge=64, le=1024), db: Session = Depends(get_db)):
    if not verify_download_token(token, image_id):
        raise HTTPException(401, detail={"code": "invalid_download_token", "message": "Der Download-Link ist ungültig oder abgelaufen."})
    image = db.get(GenerationImage, image_id)
    if not image:
        raise HTTPException(404, detail={"code": "image_not_found", "message": "Bild nicht gefunden."})
    storage = get_storage()
    if thumbnail:
        try:
            content = make_thumbnail(storage.read(image.storage_key), thumbnail)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "thumbnail_failed", "message": str(exc)}) from exc
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=1800"})
    path = storage.path(image.storage_key)
    return FileResponse(path, media_type=image.mime_type, filename=image.original_filename or f"seedream-{image.id}")


@router.get("/reference-assets", response_model=list[ReferenceAssetResponse], tags=["Referenzbild-Bibliothek"])
def list_reference_assets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(ReferenceAsset)
        .where(ReferenceAsset.user_id == user.id)
        .options(selectinload(ReferenceAsset.image))
        .order_by(ReferenceAsset.favorite.desc(), ReferenceAsset.name, ReferenceAsset.created_at.desc())
    ).all()
    return [reference_asset_dict(item) for item in items]


@router.post("/reference-assets", response_model=ReferenceAssetResponse, status_code=201, tags=["Referenzbild-Bibliothek"])
async def create_reference_asset(
    name: Annotated[str, Form(min_length=1, max_length=160)],
    category: Annotated[str | None, Form(max_length=120)] = None,
    image_id: Annotated[str | None, Form()] = None,
    image: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.scalar(select(GenerationImage).where(GenerationImage.id == image_id, GenerationImage.user_id == user.id)) if image_id else None
    if record is None and image is not None:
        settings = get_settings()
        data = await image.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, detail={"code": "file_too_large", "message": "Das Bild überschreitet die Upload-Grenze."})
        try:
            info = inspect_image(data, image.content_type)
        except ValueError as exc:
            raise HTTPException(415, detail={"code": "invalid_image", "message": str(exc)}) from exc
        key, size = get_storage().put(data, info.extension)
        record = GenerationImage(user_id=user.id, generation_id=None, storage_key=key, original_filename=(image.filename or "referenz")[:255], mime_type=info.mime_type, width=info.width, height=info.height, byte_size=size, role="reference_asset")
        db.add(record)
        db.flush()
    if record is None:
        raise HTTPException(422, detail={"code": "image_required", "message": "Bitte ein gültiges Referenzbild auswählen."})
    asset = ReferenceAsset(user_id=user.id, image_id=record.id, name=name.strip(), category=category.strip() if category else None, favorite=True)
    db.add(asset)
    db.commit()
    return reference_asset_dict(db.scalar(select(ReferenceAsset).where(ReferenceAsset.id == asset.id).options(selectinload(ReferenceAsset.image))))


@router.delete("/reference-assets/{asset_id}", status_code=204, tags=["Referenzbild-Bibliothek"])
def delete_reference_asset(asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.scalar(select(ReferenceAsset).where(ReferenceAsset.id == asset_id, ReferenceAsset.user_id == user.id))
    if not item:
        raise HTTPException(404, detail={"code": "reference_asset_not_found", "message": "Gespeichertes Referenzbild nicht gefunden."})
    db.delete(item)
    db.commit()


@router.get("/prompts", response_model=list[PromptTemplateResponse], tags=["Prompt-Bibliothek"])
def list_prompts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(PromptTemplate).where(PromptTemplate.user_id == user.id).options(selectinload(PromptTemplate.tags)).order_by(PromptTemplate.favorite.desc(), PromptTemplate.sort_order, PromptTemplate.name)).all()
    return [prompt_dict(item) for item in items]


@router.post("/prompts", response_model=PromptTemplateResponse, status_code=201, tags=["Prompt-Bibliothek"])
def create_prompt(payload: PromptTemplateInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = PromptTemplate(user_id=user.id, name=payload.name, text=payload.text, description=payload.description, category=payload.category, favorite=payload.favorite, sort_order=payload.sort_order)
    item.tags = _resolve_tags(db, user.id, payload.tags)
    db.add(item)
    db.commit()
    return prompt_dict(_load_prompt(db, item.id, user.id))


@router.put("/prompts/{prompt_id}", response_model=PromptTemplateResponse, tags=["Prompt-Bibliothek"])
def update_prompt(prompt_id: str, payload: PromptTemplateInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _load_prompt(db, prompt_id, user.id)
    for field in ("name", "text", "description", "category", "favorite", "sort_order"):
        setattr(item, field, getattr(payload, field))
    item.tags = _resolve_tags(db, user.id, payload.tags)
    db.commit()
    return prompt_dict(_load_prompt(db, item.id, user.id))


@router.post("/prompts/{prompt_id}/copy", response_model=PromptTemplateResponse, status_code=201, tags=["Prompt-Bibliothek"])
def copy_prompt(prompt_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source = _load_prompt(db, prompt_id, user.id)
    item = PromptTemplate(user_id=user.id, name=f"{source.name} – Kopie", text=source.text, description=source.description, category=source.category, favorite=False, sort_order=source.sort_order + 1, tags=list(source.tags))
    db.add(item)
    db.commit()
    return prompt_dict(_load_prompt(db, item.id, user.id))


@router.delete("/prompts/{prompt_id}", status_code=204, tags=["Prompt-Bibliothek"])
def delete_prompt(prompt_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_load_prompt(db, prompt_id, user.id))
    db.commit()


@router.get("/models", response_model=ModelsResponse, tags=["Konfiguration"])
def models(user: User = Depends(get_current_user)):
    settings = get_settings()
    aspects = ["auto", "1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"]
    return {"default_model": settings.seedream_model, "models": [
        ModelCapability(id=settings.seedream_model, name="Seedream 5.0 Pro", sizes=settings.configured_sizes, default_size=settings.seedream_default_size, max_reference_images=10, supports_layers=True, supports_interactive_edit=True, aspect_ratios=aspects),
        ModelCapability(id=settings.seedream_flash_model, name="Seedream 5.0 Flash", sizes=settings.configured_sizes, default_size=settings.seedream_default_size, max_reference_images=10, supports_layers=True, supports_interactive_edit=True, aspect_ratios=aspects),
        ModelCapability(id=settings.seedream_lite_model, name="Seedream 5.0 Lite", sizes=[size for size in settings.configured_sizes if size in {"2K", "3K", "4K"}] or ["2K"], default_size="2K", max_reference_images=14, supports_layers=False, supports_interactive_edit=False, aspect_ratios=aspects),
    ]}


@router.get("/settings", response_model=SettingsResponse, tags=["Konfiguration"])
def get_user_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    record = db.get(Setting, user.id)
    return {**SettingsResponse().model_dump(), **(record.data if record else {})}


@router.put("/settings", response_model=SettingsResponse, tags=["Konfiguration"])
def update_user_settings(payload: SettingsResponse, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    record = db.get(Setting, user.id) or Setting(user_id=user.id)
    record.data = {**(record.data or {}), **payload.model_dump()}
    db.add(record)
    db.commit()
    return payload


@router.get("/provider/config", response_model=ProviderConfigurationResponse, tags=["Konfiguration"])
def get_provider_configuration(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return provider_status(db, user.id)


@router.put("/provider/config", response_model=ProviderConfigurationResponse, tags=["Konfiguration"])
def update_provider_configuration(payload: ProviderConfigurationInput, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    save_provider_api_key(db, admin, payload.api_key.get_secret_value())
    return provider_status(db, admin.id)


@router.delete("/provider/config", response_model=ProviderConfigurationResponse, tags=["Konfiguration"])
def delete_provider_configuration(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    remove_provider_api_key(db, admin)
    return provider_status(db, admin.id)


@router.get("/api-keys", response_model=list[APIKeyResponse], tags=["API-Keys"])
def list_api_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(APIKey).where(APIKey.user_id == user.id).order_by(APIKey.created_at.desc())).all()
    return [_key_dict(item) for item in items]


@router.post("/api-keys", response_model=APIKeyCreated, status_code=201, tags=["API-Keys"])
def add_api_key(payload: APIKeyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    raw, prefix, hashed = create_api_key()
    item = APIKey(user_id=user.id, name=payload.name, key_prefix=prefix, key_hash=hashed)
    db.add(item)
    db.commit()
    return {**_key_dict(item), "key": raw}


@router.post("/api-keys/{key_id}/disable", response_model=APIKeyResponse, tags=["API-Keys"])
def disable_api_key(key_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _own_key(db, key_id, user.id)
    item.is_active = False
    db.commit()
    return _key_dict(item)


@router.post("/api-keys/{key_id}/enable", response_model=APIKeyResponse, tags=["API-Keys"])
def enable_api_key(key_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _own_key(db, key_id, user.id)
    if item.revoked_at:
        raise HTTPException(409, detail={"code": "key_revoked", "message": "Ein widerrufener Key kann nicht reaktiviert werden."})
    item.is_active = True
    db.commit()
    return _key_dict(item)


@router.delete("/api-keys/{key_id}", response_model=APIKeyResponse, tags=["API-Keys"])
def revoke_api_key(key_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _own_key(db, key_id, user.id)
    item.is_active = False
    item.revoked_at = datetime.now(UTC)
    db.commit()
    return _key_dict(item)


def _enqueue(generation_id: str) -> None:
    try:
        celery_app.send_task("seedream.process_generation", args=[generation_id])
    except Exception as exc:
        raise HTTPException(503, detail={"code": "queue_unavailable", "message": "Die Generierung wurde gespeichert, konnte aber noch nicht an den Worker übergeben werden."}) from exc


def _resolve_tags(db: Session, user_id: str, names: list[str]) -> list[PromptTemplateTag]:
    result = []
    for name in dict.fromkeys(name.strip() for name in names if name.strip()):
        tag = db.scalar(select(PromptTemplateTag).where(PromptTemplateTag.user_id == user_id, func.lower(PromptTemplateTag.name) == name.lower()))
        if not tag:
            tag = PromptTemplateTag(user_id=user_id, name=name[:80])
            db.add(tag)
            db.flush()
        result.append(tag)
    return result


def _load_prompt(db: Session, prompt_id: str, user_id: str) -> PromptTemplate:
    item = db.scalar(select(PromptTemplate).where(PromptTemplate.id == prompt_id, PromptTemplate.user_id == user_id).options(selectinload(PromptTemplate.tags)))
    if not item:
        raise HTTPException(404, detail={"code": "prompt_not_found", "message": "Prompt-Baustein nicht gefunden."})
    return item


def _own_key(db: Session, key_id: str, user_id: str) -> APIKey:
    item = db.scalar(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user_id))
    if not item:
        raise HTTPException(404, detail={"code": "api_key_not_found", "message": "API-Key nicht gefunden."})
    return item


def _key_dict(item: APIKey) -> dict:
    return {"id": item.id, "name": item.name, "prefix": f"sd_{item.key_prefix}…", "active": item.is_active, "created_at": item.created_at, "last_used_at": item.last_used_at, "revoked_at": item.revoked_at}
