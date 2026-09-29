from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class TokenResponse(BaseModel):
    access_token: str
    session_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    session_token: str


class DeviceRegistrationInput(BaseModel):
    device_token: str = Field(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")
    environment: Literal["sandbox", "production"] = "production"
    bundle_id: str = Field(default="com.example.seedream", min_length=3, max_length=255)


class DeviceRegistrationResponse(BaseModel):
    registered: bool


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")
    email: str | None = None
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=8, max_length=256)


class UserInput(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")
    email: str | None = None
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=8, max_length=256)
    is_admin: bool = False


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    email: str | None
    display_name: str
    is_admin: bool
    is_active: bool


class ImageResponse(BaseModel):
    id: str
    role: str
    mime_type: str
    width: int | None
    height: int | None
    byte_size: int
    download_url: str


class ReferenceAssetResponse(BaseModel):
    id: str
    name: str
    category: str | None
    favorite: bool
    created_at: datetime
    updated_at: datetime
    image: ImageResponse


class ReferenceResponse(BaseModel):
    id: str
    position: int
    purpose: str
    image: ImageResponse


class GenerationResponse(BaseModel):
    id: str
    parent_id: str | None
    kind: str
    status: str
    prompt: str
    final_prompt: str
    model: str
    aspect_ratio: str
    resolution: str
    seed: int | None
    parameters: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    images: list[ImageResponse] = []
    references: list[ReferenceResponse] = []
    prompt_templates: list[dict[str, Any]] = []


class GenerationList(BaseModel):
    items: list[GenerationResponse]
    total: int
    limit: int
    offset: int


class PromptTemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=12_000)
    description: str | None = Field(default=None, max_length=2_000)
    category: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=30)
    favorite: bool = False
    sort_order: int = 0


class PromptTemplateResponse(PromptTemplateInput):
    id: str
    created_at: datetime
    updated_at: datetime


class SettingsResponse(BaseModel):
    auto_save_photos: bool = False
    auto_save_album: bool = False
    album_name: str = "Seedream"
    default_model: str | None = None
    default_resolution: str | None = None


class ProviderConfigurationInput(BaseModel):
    api_key: SecretStr = Field(min_length=12, max_length=512)


class ProviderConfigurationResponse(BaseModel):
    configured: bool
    source: Literal["database", "environment", "missing"]
    endpoint: str
    model: str


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class APIKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    active: bool
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class APIKeyCreated(APIKeyResponse):
    key: str


class ModelCapability(BaseModel):
    id: str
    name: str
    sizes: list[str]
    default_size: str
    max_reference_images: int
    supports_layers: bool
    supports_interactive_edit: bool
    supports_seed: bool = False
    aspect_ratios: list[str]


class ModelsResponse(BaseModel):
    default_model: str
    models: list[ModelCapability]


class GenerateJsonRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12_000)
    final_prompt: str | None = Field(default=None, max_length=12_000)
    template_ids: list[str] = []
    image_ids: list[str] = []
    model: str | None = None
    aspect_ratio: str = "auto"
    resolution: str | None = None
    parent_id: str | None = None
    kind: Literal["generate", "edit", "variation"] = "generate"
    parameters: dict[str, Any] = {}
