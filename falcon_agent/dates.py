from __future__ import annotations

import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---", re.MULTILINE | re.DOTALL
)
_KEY_VALUE_RE = re.compile(
    r"^(?:date|updated)\s*:\s*(\d{4})-(\d{2})(?:-\d{2})?\s*$", re.MULTILINE
)
_BODY_DATE_RE = re.compile(
    r"^\s*Date\s*:\s*(\d{4})-(\d{2})-\d{2}\s*$", re.MULTILINE
)
_FILENAME_RE = re.compile(r"(\d{4})-(\d{2})")
_TIMESTAMP_RE = re.compile(r"\b(\d{4})-(\d{2})-\d{2}[T ]\d{2}:\d{2}:\d{2}(?![0-9])")


def _match_month(m: re.Match) -> str | None:
    if m is None:
        return None
    groups = m.groups()
    year = int(groups[0])
    month = int(groups[1])
    if 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    return None


def extract_month(path: str | Path, content: str) -> str | None:
    raw = content or ""
    fm = _FRONTMATTER_RE.search(raw)
    if fm:
        month = _match_month(_KEY_VALUE_RE.search(fm.group(1)))
        if month:
            return month
    month = _match_month(_BODY_DATE_RE.search(raw))
    if month:
        return month
    month = _match_month(_FILENAME_RE.search(str(path)))
    if month:
        return month
    month = _match_month(_TIMESTAMP_RE.search(raw))
    if month:
        return month
    return None


def group_by_month(files: list[tuple[str, str | None]]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for path, month in files:
        if month is None:
            continue
        grouped.setdefault(month, []).append(path)
    return sorted(grouped.items())
