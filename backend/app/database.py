"""Engine, session factory and the declarative base.

The engine is created lazily so importing models (for Alembic or unit tests)
does not require a reachable database or a populated ``.env``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base; Alembic autogenerate reads ``Base.metadata``."""


def _engine_kwargs() -> dict:
    settings = get_settings()
    return {
        "pool_pre_ping": True,
        "pool_size": 2,
        "max_overflow": 1,
        "echo": False,
    }


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """One pooled engine per process (README 9.4).

    Small pools keep a single EC2 instance comfortably inside RDS connection
    limits while leaving headroom for the API and worker together.
    """
    return create_engine(get_settings().database_url, **_engine_kwargs())


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    Never hand this session to a background task: the request scope outlives
    nothing and the session is closed on response (README 13.3).
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session for background work: commits on success, rolls back on error.

    Background threads must never reuse a request's session (README 13.3), so
    the publisher and worker open their own through this helper.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Drop cached engine/session factory so new settings take effect (tests)."""
    get_session_factory.cache_clear()
    get_engine.cache_clear()
