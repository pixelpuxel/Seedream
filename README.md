# Seedream Backend

Self-hosted FastAPI backend for Seedream image generation. It provides username/password authentication, per-user Ark API-key configuration, prompt and reference-image libraries, persistent history, generation/edit/variation jobs, local image storage, a PostgreSQL schema with Alembic migrations, Redis rate limiting, and Celery workers.

The provider adapter follows the official BytePlus ModelArk image-generation endpoint and supports Seedream 5.0 Pro, Flash, and Lite with single or multiple reference images.

## Quick start

Requirements: Docker Engine with Docker Compose v2.

```bash
cp .env.example .env
# Replace every placeholder and optionally set ARK_API_KEY.
docker compose up -d --build
curl http://localhost:8000/health/ready
```

OpenAPI is available at `http://localhost:8000/api/docs`.

Create the first administrator exactly once:

```bash
curl -X POST http://localhost:8000/api/v1/setup/initialize \
  -H 'Content-Type: application/json' \
  -H "X-Setup-Token: $SETUP_TOKEN" \
  -d '{"username":"admin","display_name":"Administrator","password":"replace-this-password"}'
```

The response contains session credentials. Login later with `POST /api/v1/auth/login`. A provider key can be supplied globally through `ARK_API_KEY` or stored per administrator through `PUT /api/v1/provider/config`.

## Production

- Put the API behind an HTTPS reverse proxy.
- Set `PUBLIC_BASE_URL` and `CORS_ORIGINS` to your own domain.
- Use independent random values for `POSTGRES_PASSWORD`, `JWT_SECRET`, `API_KEY_PEPPER`, and `SETUP_TOKEN`.
- Back up both PostgreSQL and the `image_storage` volume.
- Never commit `.env`, Ark keys, APNs keys, session tokens, or generated image data.
- If APNs is not required, leave all `APNS_*` values empty.

## Development

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

## License

MIT. See [LICENSE](LICENSE).

