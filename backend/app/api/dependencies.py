from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token, hash_api_key
from app.database import get_db
from app.models import APIKey, User


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if x_api_key or (bearer and bearer.startswith("sd_")):
        raw = x_api_key or bearer or ""
        api_key = db.scalar(select(APIKey).where(APIKey.key_hash == hash_api_key(raw), APIKey.is_active.is_(True)))
        if api_key and api_key.revoked_at is None:
            user = db.get(User, api_key.user_id)
            if user and user.is_active:
                api_key.last_used_at = datetime.now(UTC)
                db.commit()
                request.state.api_key_id = api_key.id
                return user
    if bearer:
        try:
            user_id = decode_access_token(bearer)
            user = db.get(User, user_id)
            if user and user.is_active:
                return user
        except jwt.PyJWTError:
            pass
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "unauthorized", "message": "Anmeldung oder API-Key erforderlich."})


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Administratorrechte erforderlich."})
    return user
