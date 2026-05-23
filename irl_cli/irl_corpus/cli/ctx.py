"""Shared CLI context: config, session factory, storage."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from irl_corpus.config import Config
from irl_corpus.db import get_session_factory
from irl_corpus.storage import Storage


@lru_cache(maxsize=1)
def get_config(config_path: str | None = None) -> Config:
    """Return the loaded Config, cached after first call."""
    return Config.load(Path(config_path) if config_path else None)


def get_factory(cfg: Config | None = None) -> sessionmaker:
    """Return a sessionmaker for the configured database URL."""
    if cfg is None:
        cfg = get_config()
    return get_session_factory(cfg.database.url)


def get_storage(cfg: Config | None = None) -> Storage:
    """Return a Storage instance for the configured backend."""
    if cfg is None:
        cfg = get_config()
    return Storage.from_config(cfg.storage)


def whoami(cfg: Config | None = None) -> str:
    """Return the current annotator name from config."""
    if cfg is None:
        cfg = get_config()
    return cfg.annotator.name
