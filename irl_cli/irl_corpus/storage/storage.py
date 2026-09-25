"""Unified storage abstraction using fsspec.

Works transparently for:
- Local filesystem  (backend="local", root="/mnt/secure_hdd/twitch")
- OVH / S3-compatible (backend="s3", bucket="...", endpoint="...")
"""

from __future__ import annotations

from pathlib import Path
from typing import IO

import fsspec

from irl_corpus.config import StorageConfig


class Storage:
    """Thin wrapper around an fsspec filesystem providing a fixed root prefix."""

    def __init__(self, filesystem: fsspec.AbstractFileSystem, root: str) -> None:
        self.filesystem = filesystem
        self.root = root.rstrip("/")

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, cfg: StorageConfig) -> "Storage":
        """Construct a Storage instance from a StorageConfig."""
        if cfg.backend == "local":
            filesystem = fsspec.filesystem("file")
            root = str(Path(cfg.root).resolve())
            filesystem.makedirs(root, exist_ok=True)
            return cls(filesystem, root)

        if cfg.backend == "sftp":
            kwargs: dict = {"host": cfg.host, "username": cfg.username}
            if cfg.port:
                kwargs["port"] = cfg.port
            if cfg.key_filename:
                kwargs["key_filename"] = str(Path(cfg.key_filename).expanduser())
            if cfg.password:
                kwargs["password"] = cfg.password
            filesystem = fsspec.filesystem("sftp", **kwargs)
            return cls(filesystem, cfg.root)

        if cfg.backend == "s3":
            # S3 / OVH Object Storage
            kwargs: dict = {}
            if cfg.access_key:
                kwargs["key"] = cfg.access_key
            if cfg.secret_key:
                kwargs["secret"] = cfg.secret_key
            if cfg.endpoint:
                kwargs["endpoint_url"] = cfg.endpoint
                kwargs["client_kwargs"] = {"endpoint_url": cfg.endpoint}
            filesystem = fsspec.filesystem("s3", **kwargs)
            root = f"s3://{cfg.bucket}"
            return cls(filesystem, root)
            
        raise ValueError(
            f"Unknown storage backend: {cfg.backend!r} (expected local, sftp or s3)."
        )

    # ── internal path helper ──────────────────────────────────────────────────

    def _full(self, path: str) -> str:
        return f"{self.root}/{path.lstrip('/')}"

    # ── public API ────────────────────────────────────────────────────────────

    def exists(self, path: str) -> bool:
        """Return True if the given path exists in storage."""
        return self.filesystem.exists(self._full(path))

    def open(self, path: str, mode: str = "rb") -> IO:
        """Open a file in storage and return a file-like object."""
        return self.filesystem.open(self._full(path), mode)

    def size(self, path: str) -> int | None:
        """Return the size of a stored file in bytes, or None if unavailable."""
        try:
            return self.filesystem.info(self._full(path)).get("size")
        except FileNotFoundError:
            return None

    def put(self, local_path: str | Path, remote_path: str) -> None:
        """Upload a local file to storage."""
        self.makedirs(str(Path(remote_path).parent))
        self.filesystem.put(str(local_path), self._full(remote_path))

    def get(self, remote_path: str, local_path: str | Path) -> None:
        """Download a file from storage to a local path."""
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.filesystem.get(self._full(remote_path), str(local_path))

    def delete(self, path: str) -> None:
        """Delete a file from storage."""
        self.filesystem.rm(self._full(path))

    def list_prefix(self, prefix: str) -> list[str]:
        """List all paths under a prefix, returning relative paths."""
        full = self._full(prefix)
        try:
            paths = self.filesystem.ls(full, detail=False)
        except (FileNotFoundError, PermissionError):
            return []
        return [p.removeprefix(self.root + "/") for p in paths]

    def makedirs(self, path: str) -> None:
        """Create directories in storage (no-op for object storage)."""
        try:
            self.filesystem.makedirs(self._full(path), exist_ok=True)
        except NotImplementedError:
            pass  # object storage doesn't need mkdir

    def url(self, path: str) -> str:
        """Return the full URL / path for a stored file."""
        return self._full(path)

    def delete_tree(self, prefix: str) -> None:
        """Recursively delete all files and directories under a prefix."""
        full = self._full(prefix)
        try:
            self.filesystem.rm(full, recursive=True)
        except FileNotFoundError:
            pass

    def local_path(self, path: str) -> Path | None:
        """Return a local Path if this is a local storage backend, else None."""
        protocol = self.filesystem.protocol
        protocols = (protocol,) if isinstance(protocol, str) else protocol
        if any(p in ("file", "local") for p in protocols):
            return Path(self._full(path))
        return None
