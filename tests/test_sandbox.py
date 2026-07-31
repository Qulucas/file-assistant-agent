from __future__ import annotations

import os
from pathlib import Path

import pytest

from falcon_agent.sandbox import SandboxViolation, ToolError, WorkspaceSandbox


@pytest.fixture
def sandbox(tmp_path: Path) -> WorkspaceSandbox:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("world")
    return WorkspaceSandbox(root)


def test_resolve_within_root(sandbox: WorkspaceSandbox):
    p = sandbox.resolve("a.txt")
    assert p.name == "a.txt"
    assert p.parent == sandbox.root


def test_resolve_nested(sandbox: WorkspaceSandbox):
    p = sandbox.resolve("sub/b.txt")
    assert p.name == "b.txt"
    assert p.parent == sandbox.root / "sub"


def test_resolve_relative_same_as_absolute(sandbox: WorkspaceSandbox):
    a = sandbox.resolve("sub/b.txt")
    b = sandbox.resolve(str(sandbox.root / "sub" / "b.txt"))
    assert a == b


def test_resolve_absolute_inside_root_allowed(sandbox: WorkspaceSandbox):
    p = sandbox.resolve(str(sandbox.root / "a.txt"))
    assert p.name == "a.txt"


def test_dotdot_escape_rejected(sandbox: WorkspaceSandbox):
    with pytest.raises(SandboxViolation):
        sandbox.resolve("../outside.txt")


def test_deep_dotdot_escape_rejected(sandbox: WorkspaceSandbox):
    with pytest.raises(SandboxViolation):
        sandbox.resolve("sub/../../outside.txt")


def test_absolute_outside_root_rejected(sandbox: WorkspaceSandbox, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SandboxViolation):
        sandbox.resolve(str(outside / "x.txt"))


def test_symlink_escape_rejected(sandbox: WorkspaceSandbox, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    os.symlink(outside, sandbox.root / "evil")
    with pytest.raises(SandboxViolation):
        sandbox.resolve("evil/secret.txt")


def test_dotdirs_normalized(sandbox: WorkspaceSandbox):
    p = sandbox.resolve("./sub/./b.txt")
    assert p == sandbox.root / "sub" / "b.txt"


def test_list_dir(sandbox: WorkspaceSandbox):
    entries = sandbox.list_dir(".")
    assert "a.txt" in entries
    assert "sub" in entries


def test_walk_files_returns_relative_paths(sandbox: WorkspaceSandbox):
    files = sandbox.walk_files(".")
    assert set(files) == {"a.txt", "sub/b.txt"}


def test_read_text(sandbox: WorkspaceSandbox):
    assert sandbox.read_text("a.txt") == "hello"


def test_read_missing_file_raises(sandbox: WorkspaceSandbox):
    with pytest.raises(ToolError):
        sandbox.read_text("nope.txt")


def test_write_creates_parent_dirs(sandbox: WorkspaceSandbox):
    sandbox.write_text("deep/dir/c.txt", "content")
    assert (sandbox.root / "deep" / "dir" / "c.txt").read_text() == "content"


def test_move_within_root(sandbox: WorkspaceSandbox):
    sandbox.move("a.txt", "sub/moved.txt")
    assert not (sandbox.root / "a.txt").exists()
    assert (sandbox.root / "sub" / "moved.txt").read_text() == "hello"


def test_move_refuses_overwrite(sandbox: WorkspaceSandbox):
    with pytest.raises(ToolError):
        sandbox.move("a.txt", "sub/b.txt")


def test_move_overwrite_flag(sandbox: WorkspaceSandbox):
    sandbox.move("a.txt", "sub/b.txt", overwrite=True)
    assert (sandbox.root / "sub" / "b.txt").read_text() == "hello"


def test_move_missing_source_raises(sandbox: WorkspaceSandbox):
    with pytest.raises(ToolError):
        sandbox.move("nope.txt", "sub/x.txt")


def test_relative_conversion(sandbox: WorkspaceSandbox):
    assert sandbox.relative(sandbox.root / "sub" / "b.txt") == "sub/b.txt"
