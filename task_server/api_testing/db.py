"""SQLAlchemy engine and transaction helpers for API testing."""

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import ApiTestingSettings


@lru_cache(maxsize=4)
def engine_for_url(database_url):
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("database_url must not be empty")
    return create_engine(database_url, pool_pre_ping=True)


def _session_factory():
    settings = ApiTestingSettings.from_env()
    if not settings.enabled:
        raise RuntimeError("API testing is disabled")
    return sessionmaker(
        bind=engine_for_url(settings.database_url),
        class_=Session,
        expire_on_commit=False,
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
