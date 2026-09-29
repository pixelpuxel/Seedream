import logging
import time
import uuid

import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("seedream.api")

app = FastAPI(
    title="Seedream Studio API",
    version="1.0.0",
    description="Öffentliche REST-API für Bildgenerierung, Bearbeitung, Prompt-Bibliothek und Verlauf.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)
app.include_router(router, prefix=settings.api_prefix)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:64]
    request.state.request_id = request_id
    started = time.monotonic()
    if request.url.path.startswith(settings.api_prefix) and not request.url.path.endswith("/content"):
        limit = settings.generation_rate_limit_per_minute if request.url.path.endswith(("/generate", "/edit", "/regenerate", "/variation")) else settings.rate_limit_per_minute
        try:
            client = redis.Redis.from_url(settings.redis_url, socket_timeout=0.2)
            identity = request.client.host if request.client else "unknown"
            key = f"rate:{identity}:{request.url.path}:{int(time.time() // 60)}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, 70)
            if count > limit:
                return JSONResponse(status_code=429, content={"error": {"code": "rate_limited", "message": "Zu viele Anfragen. Bitte kurz warten."}}, headers={"Retry-After": "60", "X-Request-ID": request_id})
        except redis.RedisError:
            logger.warning("Rate limiter unavailable", extra={"request_id": request_id})
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    logger.info("request method=%s path=%s status=%s duration_ms=%d request_id=%s", request.method, request.url.path, response.status_code, (time.monotonic() - started) * 1000, request_id)
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        detail = exc.detail
    else:
        detail = {"code": "http_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers={"X-Request-ID": getattr(request.state, "request_id", "")})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Die Anfrage enthält ungültige oder fehlende Angaben.", "details": {"fields": exc.errors()}}}, headers={"X-Request-ID": getattr(request.state, "request_id", "")})


@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def ready():
    try:
        redis.Redis.from_url(settings.redis_url, socket_timeout=0.5).ping()
        return {"status": "ready"}
    except redis.RedisError:
        return JSONResponse(status_code=503, content={"status": "not_ready"})

