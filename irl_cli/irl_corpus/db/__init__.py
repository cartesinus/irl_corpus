"""Database engine, session factory, and Alembic helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


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


def make_alembic_config(db_url: str) -> AlembicConfig:
    """Build an AlembicConfig pointing at the bundled migrations directory."""
    migrations_dir = Path(__file__).parent / "migrations"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def init_db(db_url: str) -> None:
    """Run all pending migrations (create tables on first run)."""
    cfg = make_alembic_config(db_url)
    alembic_command.upgrade(cfg, "head")
