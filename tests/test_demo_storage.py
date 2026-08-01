"""Tests for the Blob-backed storage logic using a fake BlobClient (no network)."""

from __future__ import annotations

import os

from pathlib import Path

import pytest

from demo_storage import BlobStorage, LocalStorage, seed_files


class FakeBlob:
    def __init__(self, pathname: str, url: str, data: bytes):
        self.pathname = pathname
        self.url = url
        self.data = data
        self.size = len(data)


class FakeClient:
    def __init__(self):
        self.store: dict[str, FakeBlob] = {}

    def put(self, pathname: str, data, access: str = "private", content_type: str = "application/octet-stream"):
        body = data.encode() if isinstance(data, str) else data
        self.store[pathname] = FakeBlob(pathname, f"https://blob/{pathname}", body)

    def get(self, url: str) -> bytes:
        pathname = url.removeprefix("https://blob/")
        return self.store[pathname].data

    def list_objects(self, prefix: str = ""):
        class Listing:
            def __init__(self, blobs):
                self.blobs = blobs

        return Listing([b for p, b in sorted(self.store.items()) if p.startswith(prefix)])

    def delete(self, urls: list[str]) -> None:
        for url in urls:
            pathname = url.removeprefix("https://blob/")
            self.store.pop(pathname, None)


@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def blob(fake):
    return BlobStorage.__new__(BlobStorage)  # skip network client init


def make_files() -> dict[str, bytes]:
    return {
        "data/a.csv": b"a,b\n1,2\n",
        "notes/b.md": b"# hi\n",
        "drafts/c.txt": b"x",
    }


def test_seed_only_once(fake, blob):
    blob._client = fake
    blob.prefix = "demo"
    blob.lock_ttl = 300.0
    blob.seed(make_files())
    blob.seed(make_files())
    assert len(fake.store) == 3


def test_materialize_and_sync(fake, blob, tmp_path):
    blob._client = fake
    blob.prefix = "demo"
    blob.lock_ttl = 300.0
    blob.seed(make_files())

    tmp = tmp_path / "ws"
    blob.materialize(tmp)
    assert (tmp / "data" / "a.csv").read_bytes() == b"a,b\n1,2\n"
    assert (tmp / "drafts" / "c.txt").read_bytes() == b"x"

    # agent overwrites one file, adds another
    (tmp / "data" / "a.csv").write_bytes(b"changed")
    (tmp / "new.md").write_text("# new")
    blob.sync_from(tmp)
    assert blob.read("data/a.csv") == b"changed"
    assert blob.read("new.md") == b"# new"
    # stale local-only file removed
    os.unlink(tmp / "drafts" / "c.txt")
    blob.sync_from(tmp)
    assert all("c.txt" not in b.pathname for b in fake.store.values())


def test_list_files(fake, blob):
    blob._client = fake
    blob.prefix = "demo"
    blob.lock_ttl = 300.0
    blob.seed(make_files())
    listing = blob.list_files()
    assert {f["path"] for f in listing} == {"data/a.csv", "notes/b.md", "drafts/c.txt"}
    assert {f["size"] for f in listing} == {8, 5, 1}


def test_reset(fake, blob):
    blob._client = fake
    blob.prefix = "demo"
    blob.lock_ttl = 300.0
    blob.seed(make_files())
    blob.reset({"only.txt": b"1"})
    assert list(blob.list_files()) == [{"path": "only.txt", "size": 1}]


def test_lock_acquire_release(fake, blob):
    blob._client = fake
    blob.prefix = "demo"
    blob.lock_ttl = 300.0
    blob.acquire_lock()
    with pytest.raises(Exception):
        blob.acquire_lock(timeout=0.5)
    blob.release_lock()
    blob.acquire_lock(timeout=0.5)
    blob.release_lock()


def test_local_storage_roundtrip(tmp_path):
    st = LocalStorage(tmp_path)
    st.seed({"a.txt": b"hello"})
    assert st.read("a.txt") == b"hello"
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.txt").write_bytes(b"world")
    st.sync_from(src)
    assert st.read("b.txt") == b"world"


def test_seed_files_skips_ds_store(tmp_path):
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "ok.txt").write_text("fine")
    files = seed_files(tmp_path)
    assert set(files) == {"ok.txt"}
