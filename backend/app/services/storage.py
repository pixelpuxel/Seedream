import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings


class Storage(ABC):
    @abstractmethod
    def put(self, data: bytes, suffix: str) -> tuple[str, int]: ...

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def path(self, key: str) -> Path: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root not in candidate.parents:
            raise ValueError("Ungültiger Storage-Key")
        return candidate

    def put(self, data: bytes, suffix: str) -> tuple[str, int]:
        suffix = suffix.lower().lstrip(".")
        if suffix not in {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "heic", "heif"}:
            suffix = "bin"
        key = f"{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}.{suffix}"
        destination = self._safe(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
        return key, len(data)

    def read(self, key: str) -> bytes:
        return self._safe(key).read_bytes()

    def path(self, key: str) -> Path:
        result = self._safe(key)
        if not result.is_file():
            raise FileNotFoundError(key)
        return result

    def delete(self, key: str) -> None:
        self._safe(key).unlink(missing_ok=True)


def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise RuntimeError("Der konfigurierte Storage-Adapter ist nicht installiert. Unterstützt: local")
    return LocalStorage(settings.storage_root)

