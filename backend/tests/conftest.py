import os

os.environ.update({
    "DATABASE_URL": "sqlite+pysqlite:////tmp/seedream-test.db",
    "REDIS_URL": "redis://localhost:6399/15",
    "JWT_SECRET": "test-jwt-secret-that-is-definitely-long-enough",
    "API_KEY_PEPPER": "test-api-key-pepper-that-is-long-enough",
    "SETUP_TOKEN": "test-setup-token-that-is-definitely-long-enough",
    "PUBLIC_BASE_URL": "http://testserver",
    "STORAGE_ROOT": "/tmp/seedream-tests-storage",
})

import pytest
from fastapi.testclient import TestClient
from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Setting, User

@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(User(id="test-user", username="test", email="test@example.com", display_name="Test", password_hash=hash_password("correct horse battery"), is_active=True, is_admin=True))
        db.add(Setting(user_id="test-user", data={"ark_api_key": "test-provider-key-not-used-on-network"}))
        db.commit()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def token(client):
    response = client.post("/api/v1/auth/login", json={"username": "test", "password": "correct horse battery"})
    assert response.status_code == 200
    return response.json()["access_token"]
