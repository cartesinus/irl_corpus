"""Configuration loading from TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StorageConfig:
    """Storage backend settings (local or S3-compatible)."""

    backend: str = "local"  # "local" | "sftp" | "s3"
    root: str = "./corpus_data"  # local path, remote path (sftp) or s3://bucket
    endpoint: str | None = None  # OVH / custom S3 endpoint URL
    bucket: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    host: str | None = None  # sftp
    username: str | None = None
    port: int = 22 
    key_filename: str | None = None
    password: str | None = None


@dataclass
class DatabaseConfig:
    """Database connection settings."""

    url: str = "sqlite:///./corpus.db"


@dataclass
class AnnotatorConfig:
    """Annotator identity settings."""

    name: str = "anonymous"


@dataclass
class ServicesConfig:
    """External service URLs (kept out of version control via irl.toml)."""

    transcribe_url: str | None = None


@dataclass
class Config:
    """Top-level application configuration."""

    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    annotator: AnnotatorConfig = field(default_factory=AnnotatorConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config from file.

        Search order:
        1. ``path`` argument (explicit)
        2. ``IRL_CONFIG`` environment variable
        3. ``./irl.toml`` in the current directory
        4. ``~/.config/irl/config.toml``
        """
        if path is None:
            env_path = os.environ.get("IRL_CONFIG")
            if env_path:
                path = Path(env_path)
            elif Path("irl.toml").exists():
                path = Path("irl.toml")
            else:
                default = Path.home() / ".config" / "irl" / "config.toml"
                if default.exists():
                    path = default

        raw: dict = {}
        if path is not None:
            with open(path, "rb") as config_file:
                raw = tomllib.load(config_file)

        storage_raw = raw.get("storage", {})
        # Allow env-var overrides for secrets
        storage = StorageConfig(
            backend=storage_raw.get("backend", "local"),
            root=storage_raw.get("root", "./corpus_data"),
            endpoint=storage_raw.get("endpoint"),
            bucket=storage_raw.get("bucket"),
            access_key=os.environ.get("IRL_STORAGE_ACCESS_KEY")
            or storage_raw.get("access_key"),
            secret_key=os.environ.get("IRL_STORAGE_SECRET_KEY")
            or storage_raw.get("secret_key"),
            host=os.environ.get("IRL_STORAGE_HOST") or storage_raw.get("host"),
            username=os.environ.get("IRL_STORAGE_USER")
            or storage_raw.get("username"),
            port=int(storage_raw.get("port", 22)),
            key_filename=os.environ.get("IRL_STORAGE_SSH_KEY")
            or storage_raw.get("key_filename"),
            password=os.environ.get("IRL_STORAGE_PASSWORD")
            or storage_raw.get("password"),
        )

        db_raw = raw.get("database", {})
        database = DatabaseConfig(
            url=os.environ.get("IRL_DB_URL")
            or db_raw.get("url", "sqlite:///./corpus.db"),
        )

        annotator_raw = raw.get("annotator", {})
        annotator = AnnotatorConfig(
            name=os.environ.get("IRL_ANNOTATOR_NAME")
            or annotator_raw.get("name", "anonymous"),
        )

        services_raw = raw.get("services", {})
        services = ServicesConfig(
            transcribe_url=os.environ.get("IRL_TRANSCRIBE_URL")
            or services_raw.get("transcribe_url"),
        )

        return cls(
            storage=storage, database=database, annotator=annotator, services=services
        )
