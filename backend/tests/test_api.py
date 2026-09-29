import io
import json
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from PIL import Image
from app.database import SessionLocal
from app.models import DeviceRegistration, Generation, GenerationImage, GenerationKind, GenerationReference, GenerationStatus, ReferenceAsset
from app.services.storage import get_storage


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, "PNG")
    return buffer.getvalue()


def test_prompt_crud_and_generation_queue(client, token):
    created = client.post("/api/v1/prompts", headers=headers(token), json={"name": "Grafit", "text": "Verwandle das Foto in eine Grafitzeichnung", "description": None, "category": "Stil", "tags": ["Zeichnung"], "favorite": True, "sort_order": 0})
    assert created.status_code == 201
    prompt = created.json()
    assert prompt["tags"] == ["Zeichnung"]
    with patch("app.api.routes.celery_app.send_task") as enqueue:
        response = client.post(
            "/api/v1/generate",
            headers=headers(token),
            data={"prompt": "Ganzkörper", "template_ids": json.dumps([prompt["id"]]), "image_ids": "[]", "aspect_ratio": "3:4", "resolution": "2K"},
            files=[("reference_images", ("reference.png", png(), "image/png"))],
        )
    assert response.status_code == 202, response.text
    generation = response.json()
    assert generation["status"] == "queued"
    assert generation["aspect_ratio"] == "3:4"
    assert generation["final_prompt"] == "Verwandle das Foto in eine Grafitzeichnung\n\nGanzkörper"
    assert len(generation["references"]) == 1
    enqueue.assert_called_once()


def test_edit_accepts_composed_final_prompt_without_free_text(client, token):
    with patch("app.api.routes.celery_app.send_task"):
        parent = client.post(
            "/api/v1/generate",
            headers=headers(token),
            data={"prompt": "Ausgangsbild", "image_ids": "[]", "aspect_ratio": "auto", "resolution": "2K"},
        )
        assert parent.status_code == 202, parent.text
        edited = client.post(
            "/api/v1/edit",
            headers=headers(token),
            data={
                "parent_id": parent.json()["id"],
                "final_prompt": "Realistische Bleistiftzeichnung",
                "template_ids": "[]",
                "image_ids": "[]",
                "aspect_ratio": "auto",
                "resolution": "2K",
            },
        )
    assert edited.status_code == 202, edited.text
    assert edited.json()["prompt"] == ""
    assert edited.json()["final_prompt"] == "Realistische Bleistiftzeichnung"


def test_api_key_can_authenticate(client, token):
    response = client.post("/api/v1/api-keys", headers=headers(token), json={"name": "Automation"})
    assert response.status_code == 201
    raw = response.json()["key"]
    authenticated = client.get("/api/v1/models", headers={"X-API-Key": raw})
    assert authenticated.status_code == 200
    assert raw not in client.get("/api/v1/api-keys", headers=headers(token)).text


def test_image_thumbnail_is_small_jpeg_and_original_remains_available(client, token):
    uploaded = client.post(
        "/api/v1/images",
        headers=headers(token),
        files={"image": ("reference.png", png(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    content_url = uploaded.json()["download_url"]
    parsed = urlsplit(content_url)
    thumbnail = client.get(f"{parsed.path}?{parsed.query}&thumbnail=64")
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert image.format == "JPEG"
        assert image.width <= 64 and image.height <= 64
    original = client.get(f"{parsed.path}?{parsed.query}")
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/png"


def test_reference_asset_library_upload_reuse_and_delete(client, token):
    created = client.post(
        "/api/v1/reference-assets",
        headers=headers(token),
        data={"name": "Gesicht Frau", "category": "Gesichter"},
        files={"image": ("face.png", png(), "image/png")},
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["name"] == "Gesicht Frau"
    assert asset["image"]["role"] == "reference_asset"

    listed = client.get("/api/v1/reference-assets", headers=headers(token))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [asset["id"]]

    reused = client.post(
        "/api/v1/reference-assets",
        headers=headers(token),
        data={"name": "Gesicht Frau Kopie", "image_id": asset["image"]["id"]},
    )
    assert reused.status_code == 201, reused.text
    assert reused.json()["image"]["id"] == asset["image"]["id"]
    assert client.delete(f"/api/v1/reference-assets/{asset['id']}", headers=headers(token)).status_code == 204


def test_session_refresh_and_logout(client):
    login = client.post("/api/v1/auth/login", json={"username": "test", "password": "correct horse battery"})
    assert login.status_code == 200
    session_token = login.json()["session_token"]
    refreshed = client.post("/api/v1/auth/refresh", json={"session_token": session_token})
    assert refreshed.status_code == 200
    assert refreshed.json()["session_token"] == session_token
    assert client.post("/api/v1/auth/logout", json={"session_token": session_token}).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"session_token": session_token}).status_code == 401


def test_push_device_can_be_registered_moved_and_disabled(client, token):
    payload = {"device_token": "ab" * 32, "environment": "production", "bundle_id": "com.example.seedream"}
    created = client.post("/api/v1/devices", headers=headers(token), json=payload)
    assert created.status_code == 200, created.text
    assert created.json() == {"registered": True}
    with SessionLocal() as db:
        device = db.query(DeviceRegistration).one()
        assert device.user_id == "test-user"
        assert device.is_active is True
    removed = client.request("DELETE", "/api/v1/devices/current", headers=headers(token), json=payload)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"registered": False}
    with SessionLocal() as db:
        assert db.query(DeviceRegistration).one().is_active is False


def test_setup_is_locked_when_user_exists(client):
    status = client.get("/api/v1/setup/status")
    assert status.status_code == 200
    response = client.post(
        "/api/v1/setup/initialize",
        headers={"X-Setup-Token": "test-setup-token-that-is-definitely-long-enough"},
        json={"username": "admin", "email": "admin@example.com", "display_name": "Admin", "password": "a secure admin password"},
    )
    assert response.status_code == 409


def test_provider_configuration_is_admin_only_and_never_returns_secret(client, token):
    secret = "provider-secret-used-only-for-this-test"
    saved = client.put("/api/v1/provider/config", headers=headers(token), json={"api_key": secret})
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["source"] == "database"
    assert secret not in saved.text
    status = client.get("/api/v1/provider/config", headers=headers(token))
    assert status.status_code == 200
    assert secret not in status.text


def test_variation_uses_result_as_first_reference_but_regenerate_keeps_original_request(client, token):
    key, size = get_storage().put(png(), "png")
    with SessionLocal() as db:
        source = Generation(
            user_id="test-user", kind=GenerationKind.generate, status=GenerationStatus.completed,
            prompt="Grafit", final_prompt="Grafit", model="dola-seedream-5-0-pro-260628",
            aspect_ratio="1:1", resolution="2K", parameters={},
        )
        db.add(source)
        db.flush()
        result = GenerationImage(
            user_id="test-user", generation_id=source.id, storage_key=key,
            original_filename="result.png", mime_type="image/png", width=32, height=32,
            byte_size=size, role="result",
        )
        db.add(result)
        db.flush()
        source_id, result_id = source.id, result.id
        db.commit()

    with patch("app.api.routes.celery_app.send_task"):
        variation = client.post(f"/api/v1/generations/{source_id}/variation", headers=headers(token))
        regenerate = client.post(f"/api/v1/generations/{source_id}/regenerate", headers=headers(token))

    assert variation.status_code == 202, variation.text
    assert variation.json()["references"][0]["image"]["id"] == result_id
    assert variation.json()["references"][0]["purpose"] == "variation_source"
    assert regenerate.status_code == 202, regenerate.text
    assert regenerate.json()["references"] == []


def test_delete_generation_removes_owned_files_but_preserves_library_images(client, token):
    disposable_key, disposable_size = get_storage().put(png(), "png")
    saved_key, saved_size = get_storage().put(png(), "png")
    with SessionLocal() as db:
        generation = Generation(
            user_id="test-user", kind=GenerationKind.generate, status=GenerationStatus.completed,
            prompt="Test", final_prompt="Test", model="dola-seedream-5-0-pro-260628",
            aspect_ratio="1:1", resolution="2K", parameters={},
        )
        db.add(generation)
        db.flush()
        disposable = GenerationImage(user_id="test-user", generation_id=generation.id, storage_key=disposable_key, original_filename="result.png", mime_type="image/png", width=32, height=32, byte_size=disposable_size, role="result")
        saved = GenerationImage(user_id="test-user", generation_id=generation.id, storage_key=saved_key, original_filename="face.png", mime_type="image/png", width=32, height=32, byte_size=saved_size, role="reference")
        db.add_all([disposable, saved])
        db.flush()
        db.add(ReferenceAsset(user_id="test-user", image_id=saved.id, name="Gesicht", favorite=True))
        generation_id, saved_id = generation.id, saved.id
        db.commit()

    response = client.delete(f"/api/v1/generations/{generation_id}", headers=headers(token))
    assert response.status_code == 200, response.text
    assert response.json()["files_deleted"] == 1
    with pytest.raises(FileNotFoundError):
        get_storage().path(disposable_key)
    assert get_storage().path(saved_key).is_file()
    with SessionLocal() as db:
        assert db.get(Generation, generation_id) is None
        assert db.get(GenerationImage, saved_id).generation_id is None
