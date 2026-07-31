import re
from pathlib import Path

import pytest

from falcon_agent.sandbox import WorkspaceSandbox
from falcon_agent.tools import MAX_RESULT_CHARS, ToolRegistry, truncate_result


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "drafts").mkdir()
    (root / "logs").mkdir()
    (root / "drafts" / "obsolete.md").write_text("---\nstatus: obsolete\n---\nold draft")
    (root / "drafts" / "active.md").write_text("---\nstatus: active\n---\nlive draft")
    (root / "notes" if False else root).joinpath("log.txt").write_text(
        "\n".join(f"2025-12-01T00:00:{i:02d}Z line {i}" for i in range(200))
    )
    (root / "logs" / "big.log").write_text(
        "\n".join(f"2025-12-01 line {i} Project Falcon event" if i == 3601 else f"2025-12-01 line {i}" for i in range(4000))
    )
    return ToolRegistry(WorkspaceSandbox(root))


def test_list_dir_flat(registry: ToolRegistry):
    out = registry.execute("list_dir", {"path": "."})
    assert "drafts/" in out
    assert "logs/" in out
    assert "log.txt" in out


def test_list_dir_recursive(registry: ToolRegistry):
    out = registry.execute("list_dir", {"path": ".", "recursive": True})
    assert "drafts/obsolete.md" in out
    assert "logs/big.log" in out


def test_read_file_pages(registry: ToolRegistry):
    out = registry.execute("read_file", {"path": "log.txt", "offset": 1, "max_lines": 5})
    assert "total_lines=200" in out
    assert "1: 2025-12-01T00:00:00Z line 0" in out
    assert "5: 2025-12-01T00:00:04Z line 4" in out
    out2 = registry.execute("read_file", {"path": "log.txt", "offset": 6, "max_lines": 2})
    assert "6: 2025-12-01T00:00:05Z line 5" in out2


def test_read_file_missing(registry: ToolRegistry):
    out = registry.execute("read_file", {"path": "nope.txt"})
    assert "Error" in out


def test_search_finds_needle_in_big_file(registry: ToolRegistry):
    out = registry.execute("search", {"pattern": "Project Falcon", "path": "logs"})
    assert "1 total match" in out
    assert "3601" in out


def test_search_case_insensitive(registry: ToolRegistry):
    (registry.sandbox.root / "case.txt").write_text("hello\nHELLO world\n")
    out = registry.execute("search", {"pattern": "hello", "path": "case.txt"})
    assert "2 match" in out


def test_search_no_match(registry: ToolRegistry):
    out = registry.execute("search", {"pattern": "zzz_nothing", "path": "."})
    assert "No matches" in out


def test_search_invalid_regex(registry: ToolRegistry):
    out = registry.execute("search", {"pattern": "(", "path": "."})
    assert "Error: invalid regex" in out


def test_write_file_creates_file(registry: ToolRegistry):
    out = registry.execute("write_file", {"path": "out/index.md", "content": "# hi"})
    assert "wrote" in out
    assert (registry.sandbox.root / "out" / "index.md").read_text() == "# hi"


def test_move_file_into_new_dir(registry: ToolRegistry):
    out = registry.execute("move_file", {"src": "drafts/obsolete.md", "dst": "archive/obsolete.md"})
    assert "moved" in out
    assert not (registry.sandbox.root / "drafts" / "obsolete.md").exists()
    assert (registry.sandbox.root / "archive" / "obsolete.md").exists()


def test_move_refuses_overwrite(registry: ToolRegistry):
    out = registry.execute("move_file", {"src": "drafts/active.md", "dst": "drafts/obsolete.md"})
    assert "Error" in out
    assert (registry.sandbox.root / "drafts" / "active.md").exists()


def test_move_escape_rejected(registry: ToolRegistry, tmp_path: Path):
    out = registry.execute("move_file", {"src": "drafts/active.md", "dst": "../evil.md"})
    assert "[BLOCKED]" in out


def test_read_escape_blocked_marker(registry: ToolRegistry):
    out = registry.execute("read_file", {"path": "../secret.txt"})
    assert "[BLOCKED]" in out
    assert "sandbox violation" in out


def test_missing_file_is_plain_error_not_blocked(registry: ToolRegistry):
    out = registry.execute("read_file", {"path": "does-not-exist.md"})
    assert "Error" in out
    assert "[BLOCKED]" not in out


def test_is_error_result(registry: ToolRegistry):
    assert registry.is_error_result("Error: x")
    assert registry.is_error_result("[BLOCKED] sandbox violation: x")
    assert not registry.is_error_result("wrote 10 bytes to a.txt")


def test_unknown_tool_raises(registry: ToolRegistry):
    with pytest.raises(KeyError):
        registry.execute("rm", {"path": "."})


def test_truncate_result():
    assert truncate_result("x" * 100, 100) == "x" * 100
    out = truncate_result("x" * 200, 100)
    assert len(out) == 100 + len("\n") + len("...(truncated, remaining output omitted)")
    assert "truncated" in out


def test_result_truncation_applied(registry: ToolRegistry):
    (registry.sandbox.root / "huge.txt").write_text("A" * (MAX_RESULT_CHARS * 2))
    out = registry.execute("read_file", {"path": "huge.txt", "max_lines": 100000})
    assert "truncated" in out
    assert len(out) < MAX_RESULT_CHARS * 2
