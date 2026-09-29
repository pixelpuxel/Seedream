from app.core.security import create_api_key, decode_access_token, hash_api_key, verify_password, hash_password, create_access_token


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("a secure password")
    second = hash_password("a secure password")
    assert first != second
    assert verify_password("a secure password", first)
    assert not verify_password("wrong", first)


def test_access_and_api_keys():
    token = create_access_token("user-123")
    assert decode_access_token(token) == "user-123"
    raw, prefix, hashed = create_api_key()
    assert raw.startswith("sd_") and prefix in raw
    assert hash_api_key(raw) == hashed
    assert raw not in hashed

