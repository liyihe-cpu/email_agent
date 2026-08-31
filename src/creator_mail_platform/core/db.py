from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import ActivityContact, MailboxCheckpoint, Base, SCHEMA


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.target_database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=5,
            max_overflow=5,
            connect_args={
                "application_name": "creator-mail-platform",
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
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
