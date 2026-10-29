import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config
from app.db import Base, engine
from app.routers import checkin, core, courses, dashboard, enrollment, live, sessions, stubs

API = "/api/v1"
PROD = config.ENV == "prod"

log = logging.getLogger("gatekeeper")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _log(**fields) -> None:
    log.info(json.dumps(fields, separators=(",", ":")) if config.LOG_JSON else " ".join(f"{k}={v}" for k, v in fields.items()))


# The interactive docs and schema are for development; production exposes neither.
app = FastAPI(title="GateKeeper", version="1.0.0", openapi_url=None if PROD else f"{API}/openapi.json", docs_url=None if PROD else "/docs", redoc_url=None)


@app.on_event("startup")
def _create_tables():
    from app import models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine)
    _log(event="startup", env=config.ENV, face_mode=config.FACE_MODE, trial_mode=config.TRIAL_MODE, redis=bool(config.REDIS_URL))
    if config.TRIAL_MODE:
        log.warning("TRIAL MODE is on: phone audio telemetry is being stored")


@app.middleware("http")
async def _wrap(request: Request, call_next):
    rid = uuid.uuid4().hex
    request.state.rid = rid
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("unhandled error rid=%s", rid)
        resp = JSONResponse({"error": {"code": "INTERNAL", "message": "Something went wrong", "request_id": rid}}, status_code=500)
    resp.headers["x-request-id"] = rid
    resp.headers.setdefault("x-content-type-options", "nosniff")
    resp.headers.setdefault("referrer-policy", "no-referrer")
    resp.headers.setdefault("cache-control", "no-store")
    resp.headers.setdefault("content-security-policy", "default-src 'none'; frame-ancestors 'none'")
    if PROD:
        resp.headers.setdefault("strict-transport-security", "max-age=31536000")
    if not request.url.path.endswith("/healthz"):
        # never the query string (the WebSocket carries a token there) and never a body
        _log(event="req", rid=rid, method=request.method, path=request.url.path, status=resp.status_code, ms=round((time.perf_counter() - t0) * 1000))
    return resp


if config.CORS_ORIGINS:  # added last so it wraps the middleware above and answers preflights itself
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["authorization", "content-type", "x-request-id"], max_age=600)


def _err(status: int, code: str, request: Request):
    return JSONResponse({"error": {"code": code, "message": code, "request_id": getattr(request.state, "rid", None) or uuid.uuid4().hex}}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException):
    code = str(exc.detail)
    if exc.status_code == 404 and code == "Not Found":
        code = "NOT_FOUND"
    elif exc.status_code == 405:
        code = "METHOD_NOT_ALLOWED"
    return _err(exc.status_code, code, request)


@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "Invalid request", "request_id": getattr(request.state, "rid", None) or uuid.uuid4().hex}}, status_code=422)


app.include_router(core.router, prefix=API)
app.include_router(courses.router, prefix=API)
app.include_router(enrollment.router, prefix=API)
# checkin first: '/sessions/active' (student) must match before '/sessions/{session_id}' (professor)
app.include_router(checkin.router, prefix=API)
app.include_router(sessions.router, prefix=API)
app.include_router(live.router, prefix=API)
app.include_router(dashboard.router, prefix=API)
app.include_router(stubs.router, prefix=API)
