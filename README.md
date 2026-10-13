# Seedream Backend

Self-hosted FastAPI backend for Seedream image generation. It provides username/password authentication, per-user Ark API-key configuration, prompt and reference-image libraries, persistent history, generation/edit/variation jobs, local image storage, a PostgreSQL schema with Alembic migrations, Redis rate limiting, and Celery workers.

The provider adapter follows the official BytePlus ModelArk image-generation endpoint and supports Seedream 5.0 Pro, Flash, and Lite with single or multiple reference images.

## Relationship to the iOS app and business model

This repository contains the optional, self-hosted server component for the independently developed Seedream Studio iOS app. The iOS app can also be used in standalone mode without this server.

Users bring their own official BytePlus ModelArk account and API key. API access, inference credits, and model usage are not included, resold, or billed by the app developer; any provider charges are paid by the user directly to the API provider. The paid App Store download covers the client application itself. Self-hosting is optional, and this backend is provided under the MIT License for users who prefer to operate their own server and storage.

Seedream Studio is an independent client and is not affiliated with, endorsed by, or an official product of ByteDance, BytePlus, or Volcano Engine. “Seedream” is used descriptively to identify compatibility with the Seedream models exposed through the official ModelArk API. All related names and trademarks belong to their respective owners.

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

