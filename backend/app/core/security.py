import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings

PBKDF2_ROUNDS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": user_id, "type": "access", "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> str:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != "access" or not payload.get("sub"):
        raise jwt.InvalidTokenError("Ungültiger Token")
    return str(payload["sub"])


def create_api_key() -> tuple[str, str, str]:
    secret = secrets.token_urlsafe(32)
    prefix = secret[:10]
    raw = f"sd_{secret}"
    return raw, prefix, hash_api_key(raw)


def create_session_token() -> tuple[str, str]:
    raw = f"sds_{secrets.token_urlsafe(48)}"
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_api_key(raw: str) -> str:
    return hmac.new(get_settings().api_key_pepper.encode(), raw.encode(), hashlib.sha256).hexdigest()


def create_download_token(image_id: str, expires_minutes: int = 30) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": image_id, "type": "image", "iat": now, "exp": now + timedelta(minutes=expires_minutes)},
        get_settings().jwt_secret,
        algorithm="HS256",
    )


def verify_download_token(token: str, image_id: str) -> bool:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return payload.get("type") == "image" and hmac.compare_digest(str(payload.get("sub")), image_id)
    except jwt.PyJWTError:
        return False
