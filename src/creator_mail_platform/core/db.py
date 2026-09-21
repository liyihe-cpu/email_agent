from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import ActivityContact, MailboxCheckpoint, Base, SCHEMA


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        database_url = make_url(settings.target_database_url)
        if settings.target_database_name:
            database_url = database_url.set(database=settings.target_database_name)
        _engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            # Batch commands can be launched in parallel. Keep each process to
            # a small, bounded connection footprint so they cannot exhaust the
            # shared PostgreSQL instance.
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            connect_args={
                "application_name": "creator-mail-platform",
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
                "connect_timeout": settings.db_connect_timeout_seconds,
            },
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def initialize_schema() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    # Core sync must never create optional follow-up tables implicitly.
    # The compact follow-up schema is initialized by its own workflow.
    Base.metadata.create_all(
        engine,
        tables=[ActivityContact.__table__, MailboxCheckpoint.__table__],
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                f"ALTER TABLE {SCHEMA}.activity_contacts "
                "ADD COLUMN IF NOT EXISTS offered_brief_codes JSONB"
            )
        )
        connection.execute(
            text(
                f"ALTER TABLE {SCHEMA}.activity_contacts "
                "ADD COLUMN IF NOT EXISTS follower_count BIGINT"
            )
        )
