from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Setting, User


PROVIDER_KEY_FIELD = "ark_api_key"


def provider_api_key(db: Session, user_id: str | None = None) -> tuple[str, str]:
    if user_id:
        record = db.get(Setting, user_id)
        key = _key(record)
        if key:
            return key, "database"

    admin_record = db.scalar(
        select(Setting)
        .join(User, User.id == Setting.user_id)
        .where(User.is_admin.is_(True), User.is_active.is_(True))
        .order_by(Setting.updated_at.desc())
    )
    key = _key(admin_record)
    if key:
        return key, "database"

    environment_key = get_settings().ark_api_key.strip()
    return (environment_key, "environment") if environment_key else ("", "missing")


def save_provider_api_key(db: Session, admin: User, api_key: str) -> None:
    record = db.get(Setting, admin.id) or Setting(user_id=admin.id, data={})
    data = dict(record.data or {})
    data[PROVIDER_KEY_FIELD] = api_key.strip()
    record.data = data
    db.add(record)
    db.commit()


def remove_provider_api_key(db: Session, admin: User) -> None:
    record = db.get(Setting, admin.id)
    if not record:
        return
    data = dict(record.data or {})
    data.pop(PROVIDER_KEY_FIELD, None)
    record.data = data
    db.commit()


def provider_status(db: Session, user_id: str | None = None) -> dict[str, object]:
    key, source = provider_api_key(db, user_id)
    settings = get_settings()
    return {
        "configured": bool(key),
        "source": source,
        "endpoint": settings.seedream_endpoint,
        "model": settings.seedream_model,
    }


def _key(record: Setting | None) -> str:
    value = (record.data or {}).get(PROVIDER_KEY_FIELD) if record else None
    return value.strip() if isinstance(value, str) else ""
