from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

import agent as agent_cli
import server as srv

ROOT = Path(__file__).resolve().parents[1]


def test_index_html_uses_relative_tree_paths():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'renderTree(data, root, "")' in html
    m = re.search(r"const childPath = (.*?);", html)
    assert m, "renderTree must define childPath"
    assert "path ?" in m.group(1)


def test_cli_steps_default_covers_public_t1():
    assert agent_cli.build_parser().get_default("steps") == 60


def test_rate_limit_applies_in_blob_mode(monkeypatch):
    monkeypatch.setattr(srv, "STORAGE_KIND", "blob")
    monkeypatch.setattr(srv, "RATE_PER_MIN", 6)
    srv._run_clock.clear()
    for _ in range(6):
        asyncio.run(srv._check_rate("1.2.3.4"))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv._check_rate("1.2.3.4"))
    assert ei.value.status_code == 429


def test_requirements_pins_pytest():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pytest" in req
