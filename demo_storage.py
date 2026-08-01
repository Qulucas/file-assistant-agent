"""Storage backends for the demo server.

Two backends implement the same minimal interface:
  * LocalStorage  -- a plain directory on disk (local dev / tests)
  * BlobStorage   -- Vercel Blob (serverless, Vercel Hobby)

The agent itself always runs against a local directory (WorkspaceSandbox).
For Blob, the server materializes the workspace into /tmp for the duration
of a run, then syncs files back to Blob. This keeps the core agent filesystem
based and untouched; only this module knows about remote storage.
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from falcon_agent.sandbox import WorkspaceSandbox


class StorageError(Exception):
    pass


class LockTimeout(StorageError):
    pass


class Storage:
    def seed(self, seed_files: dict[str, bytes]) -> None:
        raise NotImplementedError

    def reset(self, seed_files: dict[str, bytes]) -> None:
        raise NotImplementedError

    def materialize(self, dst_dir: Path) -> None:
        raise NotImplementedError

    def sync_from(self, src_dir: Path) -> None:
        raise NotImplementedError

    def list_files(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def read(self, rel: str) -> bytes:
        raise NotImplementedError

    def acquire_lock(self, timeout: float = 10.0) -> None:
        raise NotImplementedError

    def release_lock(self) -> None:
        raise NotImplementedError


def _read_tree(base: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != ".DS_Store":
            files[str(p.relative_to(base))] = p.read_bytes()
    return files


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._lock_path = self.root.parent / f".{self.root.name}.lock"

    def seed(self, seed_files: dict[str, bytes]) -> None:
        if self.root.exists() and any(self.root.rglob("*")):
            return
        self._write_all(seed_files)

    def reset(self, seed_files: dict[str, bytes]) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)
        self._write_all(seed_files)

    def _write_all(self, files: dict[str, bytes]) -> None:
        for rel, data in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def materialize(self, dst_dir: Path) -> None:
        for rel in self.list_rels():
            p = self.root / rel
            dst = dst_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p.read_bytes())

    def sync_from(self, src_dir: Path) -> None:
        import shutil

        files = _read_tree(src_dir)
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def list_rels(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file() and p.name != ".DS_Store"
        )

    def list_files(self) -> list[dict[str, Any]]:
        out = []
        for rel in self.list_rels():
            out.append({"path": rel, "size": (self.root / rel).stat().st_size})
        return out

    def read(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()

    def acquire_lock(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                time.sleep(0.2)
        raise LockTimeout("another run is in progress")

    def release_lock(self) -> None:
        try:
            os.unlink(self._lock_path)
        except FileNotFoundError:
            pass


class BlobStorage(Storage):
    """Vercel Blob backed storage.

    Blob is a flat key-value object store; a workspace maps to blobs whose
    pathname starts with a unique run/workspace prefix. We keep a single
    "demo workspace" set of blobs under prefix ``demo/``.
    """

    def __init__(self, token: str, prefix: str = "demo", lock_ttl: float = 300.0):
        from vercel.blob import BlobClient

        self._client = BlobClient(token=token)
        self.prefix = prefix.rstrip("/")
        self.lock_ttl = lock_ttl

    def _path(self, rel: str) -> str:
        return f"{self.prefix}/{rel}"

    def _objects(self, prefix: str) -> list[Any]:
        try:
            listing = self._client.list_objects(prefix=prefix)
        except Exception as exc:
            raise StorageError(f"blob list failed: {exc}") from exc
        return list(getattr(listing, "blobs", []) or [])

    def seed(self, seed_files: dict[str, bytes]) -> None:
        if self._objects(f"{self.prefix}/"):
            return
        self._put_all(seed_files)

    def reset(self, seed_files: dict[str, bytes]) -> None:
        self._delete_all()
        self._put_all(seed_files)

    def _put_all(self, files: dict[str, bytes]) -> None:
        for rel, data in files.items():
            self._client.put(
                self._path(rel),
                data,
                access="private",
                content_type="application/octet-stream",
            )

    def _delete_all(self) -> None:
        objs = self._objects(f"{self.prefix}/")
        urls = [b.url for b in objs]
        if urls:
            self._client.delete(urls)

    def materialize(self, dst_dir: Path) -> None:
        for blob in self._objects(f"{self.prefix}/"):
            rel = blob.pathname[len(self.prefix) + 1 :]
            data = self._client.get(blob.url)
            p = dst_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def sync_from(self, src_dir: Path) -> None:
        files = _read_tree(src_dir)
        self._put_all(files)
        remote = {b.pathname[len(self.prefix) + 1 :] for b in self._objects(f"{self.prefix}/")}
        stale = [self._path(r) for r in remote - set(files)]
        if stale:
            objs = [b for b in self._objects(f"{self.prefix}/") if b.pathname in set(stale)]
            if objs:
                self._client.delete([b.url for b in objs])

    def list_files(self) -> list[dict[str, Any]]:
        return [
            {"path": b.pathname[len(self.prefix) + 1 :], "size": b.size}
            for b in self._objects(f"{self.prefix}/")
        ]

    def read(self, rel: str) -> bytes:
        blob = self._find(self._path(rel))
        if blob is None:
            raise StorageError(f"blob not found: {rel!r}")
        return self._client.get(blob.url)

    def _find(self, pathname: str) -> Any:
        for b in self._objects(f"{self.prefix}/"):
            if b.pathname == pathname:
                return b
        return None

    def acquire_lock(self, timeout: float = 10.0) -> None:
        lock_path = f"{self.prefix}/.lock"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            existing = self._find(lock_path)
            if existing is None:
                try:
                    self._client.put(
                        lock_path,
                        f"{time.time()}".encode(),
                        access="private",
                        content_type="text/plain",
                    )
                    return
                except Exception:
                    time.sleep(0.3)
                    continue
            try:
                created = float(self._client.get(existing.url).decode())
            except Exception:
                created = 0.0
            if time.time() - created > self.lock_ttl:
                self._client.delete([existing.url])
                continue
            time.sleep(0.5)
        raise LockTimeout("another run is in progress")

    def release_lock(self) -> None:
        lock_path = f"{self.prefix}/.lock"
        existing = self._find(lock_path)
        if existing is not None:
            try:
                self._client.delete([existing.url])
            except Exception:
                pass


def make_storage(kind: str, base_dir: Path, token: str = "") -> Storage:
    if kind == "blob":
        return BlobStorage(token)
    root = Path(os.environ.get("DEMO_WORKSPACE", str(base_dir / "demo_workspace")))
    return LocalStorage(root)


def seed_files(source: Path) -> dict[str, bytes]:
    return _read_tree(source)
