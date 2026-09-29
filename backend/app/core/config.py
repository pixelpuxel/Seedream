from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "Seedream Studio"
    environment: str = "production"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://seedream:seedream@postgres:5432/seedream"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = Field(min_length=32)
    api_key_pepper: str = Field(min_length=32)
    jwt_ttl_minutes: int = 15
    setup_token: str = Field(min_length=32)
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:8000"

    storage_backend: str = "local"
    storage_root: Path = Path("/data/storage")
    max_upload_bytes: int = 30 * 1024 * 1024

    ark_api_key: str = ""
    seedream_endpoint: str = "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations"
    seedream_model: str = "dola-seedream-5-0-pro-260628"
    seedream_flash_model: str = "dola-seedream-5-0-flash-260915"
    seedream_lite_model: str = "seedream-5-0-lite-260128"
    seedream_sizes: str = "1K,1.5K,2K"
    seedream_default_size: str = "2K"
    seedream_watermark: bool = False
    provider_timeout_seconds: int = 300

    apns_team_id: str = ""
    apns_key_id: str = ""
    apns_private_key_b64: str = ""
    apns_topic: str = "com.example.seedream"

    rate_limit_per_minute: int = 60
    generation_rate_limit_per_minute: int = 10

    @field_validator("cors_origins")
    @classmethod
    def normalize_origins(cls, value: str) -> str:
        return ",".join(item.strip().rstrip("/") for item in value.split(",") if item.strip())

    @model_validator(mode="after")
    def reject_placeholder_secrets(self):
        if self.environment == "production":
            for name, value in (("JWT_SECRET", self.jwt_secret), ("API_KEY_PEPPER", self.api_key_pepper), ("SETUP_TOKEN", self.setup_token)):
                if "CHANGE_ME" in value or "REPLACE" in value:
                    raise ValueError(f"{name} enthält noch einen Platzhalter")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return self.cors_origins.split(",")

    @property
    def configured_sizes(self) -> list[str]:
        return [item.strip() for item in self.seedream_sizes.split(",") if item.strip()]

    @property
    def apns_configured(self) -> bool:
        return bool(self.apns_team_id and self.apns_key_id and self.apns_private_key_b64 and self.apns_topic)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
