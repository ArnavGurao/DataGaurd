"""FastAPI application entrypoint.

Run locally from ``backend``::

    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .database import session_scope
from .errors import CODE_STORAGE_UNAVAILABLE, error_body, register_exception_handlers
from .routers import auth, downloads, jobs, rules
from .services.dispatcher import Publisher
from .services.notifier import get_notifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("dataguard.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the publisher for the life of the process (README 13.3).

    It runs in its own thread with its own database sessions, so blocking
    database and (later) Boto3 calls never touch the event loop.
    """
    get_settings()  # fail fast on missing or placeholder configuration

    publisher = Publisher(notifier=get_notifier())
    publisher.start()
    app.state.publisher = publisher
    try:
        yield
    finally:
        publisher.stop()


app = FastAPI(title="DataGuard API", version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(rules.router)
app.include_router(jobs.router)
app.include_router(downloads.router)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness only: answers even when the database is down."""
    return {"status": "ok", "service": "dataguard-api"}


@app.get("/api/ready", tags=["health"])
def ready() -> JSONResponse:
    """Readiness: is the database usable right now?

    Deliberately silent about *why* it failed — no connection string, password
    or traceback reaches the client (README 9.2).

    The 503 uses the one structured error envelope (README 9.7); the 200 is a
    success response and stays bare, unwrapped by any ``error`` object.
    """
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Readiness check failed: the database is unavailable.", exc_info=True)
        return JSONResponse(
            status_code=503,
            content=error_body(
                CODE_STORAGE_UNAVAILABLE,
                "The database is unavailable. Try again shortly.",
            ),
        )
    return JSONResponse(status_code=200, content={"status": "ok", "database": "ok"})
