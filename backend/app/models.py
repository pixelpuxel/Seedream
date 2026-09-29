import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class GenerationStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class GenerationKind(str, enum.Enum):
    generate = "generate"
    edit = "edit"
    variation = "variation"
    regenerate = "regenerate"


prompt_template_tags = Table(
    "prompt_template_tags",
    Base.metadata,
    Column("prompt_template_id", ForeignKey("prompt_templates.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("prompt_template_tag.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SetupState(Base):
    __tablename__ = "setup_state"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="singleton")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class APIKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceRegistration(Base):
    __tablename__ = "device_registrations"
    __table_args__ = (UniqueConstraint("device_token"), Index("ix_device_registrations_user_active", "user_id", "is_active"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_token: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production")
    bundle_id: Mapped[str] = mapped_column(String(255), default="com.example.seedream")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Generation(Base):
    __tablename__ = "generations"
    __table_args__ = (Index("ix_generations_user_created", "user_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("generations.id", ondelete="SET NULL"), index=True)
    kind: Mapped[GenerationKind] = mapped_column(Enum(GenerationKind), default=GenerationKind.generate)
    status: Mapped[GenerationStatus] = mapped_column(Enum(GenerationStatus), default=GenerationStatus.queued, index=True)
    prompt: Mapped[str] = mapped_column(Text)
    final_prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(160))
    aspect_ratio: Mapped[str] = mapped_column(String(32), default="auto")
    resolution: Mapped[str] = mapped_column(String(32))
    seed: Mapped[int | None] = mapped_column(Integer)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent: Mapped["Generation | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Generation"]] = relationship(back_populates="parent")
    images: Mapped[list["GenerationImage"]] = relationship(back_populates="generation", cascade="all, delete-orphan")
    references: Mapped[list["GenerationReference"]] = relationship(back_populates="generation", cascade="all, delete-orphan", order_by="GenerationReference.position")
    template_links: Mapped[list["GenerationPromptTemplate"]] = relationship(cascade="all, delete-orphan", order_by="GenerationPromptTemplate.position")


class GenerationImage(Base):
    __tablename__ = "generation_images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    generation_id: Mapped[str | None] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(80))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(40), default="result")
    provider_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generation: Mapped[Generation | None] = relationship(back_populates="images")


class ReferenceAsset(Base):
    __tablename__ = "reference_assets"
    __table_args__ = (Index("ix_reference_assets_user_created", "user_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    image_id: Mapped[str] = mapped_column(ForeignKey("generation_images.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(120), index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    image: Mapped[GenerationImage] = relationship()


class GenerationReference(Base):
    __tablename__ = "generation_references"
    __table_args__ = (UniqueConstraint("generation_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), index=True)
    image_id: Mapped[str] = mapped_column(ForeignKey("generation_images.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(40), default="reference")
    generation: Mapped[Generation] = relationship(back_populates="references")
    image: Mapped[GenerationImage] = relationship()


class PromptTemplateTag(Base):
    __tablename__ = "prompt_template_tag"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("user_id", "name"),)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(120), index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    tags: Mapped[list[PromptTemplateTag]] = relationship(secondary=prompt_template_tags, lazy="selectin")


class GenerationPromptTemplate(Base):
    __tablename__ = "generation_prompt_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_templates.id", ondelete="SET NULL"), nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    name_snapshot: Mapped[str] = mapped_column(String(160))
    text_snapshot: Mapped[str] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProviderRequest(Base):
    __tablename__ = "provider_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), unique=True)
    endpoint: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderResponse(Base):
    __tablename__ = "provider_responses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), unique=True)
    status_code: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    responded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
