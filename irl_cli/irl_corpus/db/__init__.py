"""Database engine, session factory, and schema creation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from irl_corpus.db.models import Base


def get_engine(url: str) -> Engine:
    """Create and return a SQLAlchemy engine for the given database URL."""
    engine = create_engine(url)
    # Enable WAL mode and foreign keys for SQLite
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

    return engine


def get_session_factory(url: str) -> sessionmaker:
    """Return a sessionmaker bound to a new engine for the given URL."""
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker) -> Generator[Session, None, None]:
    """Context manager that commits on success and rolls back on exception."""
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(db_url: str) -> None:
    """Create all tables from the current models (no-op if they already exist).

    There's no production database yet, so schema changes are made directly
    on the models rather than tracked through migrations; this just (re)syncs
    tables to whatever the models currently declare.
    """
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
