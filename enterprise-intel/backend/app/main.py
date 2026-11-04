import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api import auth, documents, search
from app.config import settings
from app.db.session import engine
from app.ratelimit import limiter

logger = logging.getLogger("app")

app = FastAPI(title="Enterprise Intelligence Platform", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 413: "too_large"}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error_response(exc.status_code, CODES.get(exc.status_code, "error"), str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return error_response(422, "validation_error", f"{where}: {first.get('msg', 'invalid request')}".strip(": "))


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.exception("unhandled error request_id=%s", getattr(request.state, "request_id", "-"))
    return error_response(500, "internal_error", "Internal server error")  # never leak stack traces


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000)
    response.headers["x-request-id"] = request.state.request_id
    logger.info(
        "request_id=%s method=%s path=%s status=%s latency_ms=%s",
        request.state.request_id, request.method, request.url.path, response.status_code, latency_ms,
    )
    return response


@app.get("/health", tags=["health"])
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return error_response(503, "db_unavailable", "Database unavailable")
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)
