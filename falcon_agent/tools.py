from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from falcon_agent.sandbox import SandboxViolation, ToolError, WorkspaceSandbox

MAX_RESULT_CHARS = 6000
TRUNCATED_MARK = "...(truncated, remaining output omitted)"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def truncate_result(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n" + TRUNCATED_MARK


class ToolRegistry:
    def __init__(self, sandbox: WorkspaceSandbox, result_limit: int = MAX_RESULT_CHARS):
        self.sandbox = sandbox
        self.result_limit = result_limit
        self._tools: dict[str, Tool] = {}
        self._register_all()

    def _register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def _register_all(self) -> None:
        self._register(Tool(
            name="list_dir",
            description=(
                "List entries in a directory. Returns entry names; use "
                "recursive=true to list all files below a directory as "
                "relative paths. Paths are relative to the workspace root."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": ".", "description": "directory path relative to workspace root"},
                    "recursive": {"type": "boolean", "default": False},
                },
                "required": [],
            },
            handler=self._list_dir,
        ))
        self._register(Tool(
            name="read_file",
            description=(
                "Read a text file. Returns up to max_lines lines starting at "
                "line offset (1-based). Use offset to page through large files; "
                "never rely on reading a huge file in one call."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file path relative to workspace root"},
                    "offset": {"type": "integer", "default": 1, "description": "1-based start line"},
                    "max_lines": {"type": "integer", "default": 150},
                },
                "required": ["path"],
            },
            handler=self._read_file,
        ))
        self._register(Tool(
            name="search",
            description=(
                "Regex-search file contents under a path (default: whole "
                "workspace). Case-insensitive. Returns matching lines with "
                "line numbers and a count of total matches; results are "
                "capped. Use this for finding needles in large files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "regex or literal text to search for"},
                    "path": {"type": "string", "default": ".", "description": "directory or file to search under, relative to workspace root"},
                    "max_results": {"type": "integer", "default": 50},
                    "context_lines": {"type": "integer", "default": 0, "description": "lines of context to include around each match"},
                },
                "required": ["pattern"],
            },
            handler=self._search,
        ))
        self._register(Tool(
            name="write_file",
            description=(
                "Write text content to a file (creates parent directories). "
                "Overwrites existing content. Path must stay inside the "
                "workspace."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file path relative to workspace root"},
                    "content": {"type": "string", "description": "full text content to write"},
                },
                "required": ["path", "content"],
            },
            handler=self._write_file,
        ))
        self._register(Tool(
            name="move_file",
            description=(
                "Move or rename a file within the workspace. Creates the "
                "destination parent directory if missing. Refuses to "
                "overwrite an existing destination unless overwrite=true."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "source path relative to workspace root"},
                    "dst": {"type": "string", "description": "destination path relative to workspace root"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["src", "dst"],
            },
            handler=self._move_file,
        ))

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].schema() for name in self.names()]

    def execute(self, name: str, args: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown tool: {name!r}")
        try:
            raw = tool.handler(**args)
        except SandboxViolation as exc:
            raw = f"[BLOCKED] sandbox violation: {exc}"
        except ToolError as exc:
            raw = f"Error: {exc}"
        except Exception as exc:
            raw = f"Error: {exc}"
        return truncate_result(raw, self.result_limit)

    def is_error_result(self, result: str) -> bool:
        return result.startswith(("Error:", "[BLOCKED]"))

    def _list_dir(self, path: str = ".", recursive: bool = False) -> str:
        if recursive:
            files = self.sandbox.walk_files(path)
            if not files:
                return "(directory is empty)"
            lines = [f"Found {len(files)} files under {path or '.'}:"] + files
            return "\n".join(lines)
        entries = self.sandbox.list_dir(path)
        if not entries:
            return "(directory is empty)"
        lines = []
        for e in entries:
            full = f"{path}/{e}".lstrip("/") if path not in (".", "") else e
            marker = "/" if self.sandbox.is_dir(full) else ""
            lines.append(f"{e}{marker}")
        return "\n".join(lines)

    def _read_file(self, path: str, offset: int = 1, max_lines: int = 150) -> str:
        resolved = self.sandbox.resolve(path)
        if resolved.is_dir():
            return f"Error: {path!r} is a directory, not a file"
        total = sum(1 for _ in resolved.open(encoding="utf-8", errors="replace"))
        lines: list[str] = []
        with resolved.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if i < offset:
                    continue
                if i >= offset + max_lines:
                    break
                lines.append(f"{i}: {line.rstrip(chr(10)).rstrip(chr(13))}")
        out = "\n".join(lines)
        header = f"path={path} total_lines={total} lines {offset}-{min(offset + max_lines - 1, total)}"
        return header + "\n" + out

    def _search(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 50,
        context_lines: int = 0,
    ) -> str:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"Error: invalid regex {pattern!r}: {exc}"
        target = self.sandbox.resolve(path)
        if target.is_file():
            files = [path]
        else:
            files = self.sandbox.walk_files(path)
        out: list[str] = []
        total_matches = 0
        matched_files = 0
        shown = 0
        for rel in files:
            abs_path = self.sandbox.resolve(rel)
            if abs_path.is_dir():
                continue
            with abs_path.open(encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
            hits = [(i + 1, l) for i, l in enumerate(all_lines) if regex.search(l)]
            if not hits:
                continue
            matched_files += 1
            total_matches += len(hits)
            out.append(f"== {rel}: {len(hits)} match(es)")
            for lineno, line in hits:
                if shown >= max_results:
                    break
                shown += 1
                if context_lines > 0:
                    lo = max(1, lineno - context_lines)
                    hi = min(len(all_lines), lineno + context_lines)
                    for i in range(lo, hi + 1):
                        prefix = ">>" if i == lineno else "  "
                        out.append(f"  {prefix} {i}: {all_lines[i - 1].rstrip()}")
                else:
                    out.append(f"  {lineno}: {line.rstrip()}")
            if shown >= max_results:
                break
        if total_matches == 0:
            return f"No matches for pattern {pattern!r} under {path!r}"
        result = "\n".join(out)
        if total_matches > shown:
            result += f"\n...({total_matches - shown} more matches not shown)"
        summary = (
            f"{total_matches} total match(es) across {matched_files} file(s); "
            f"{shown} shown"
        )
        return summary + "\n" + result

    def _write_file(self, path: str, content: str) -> str:
        resolved = self.sandbox.write_text(path, content)
        return f"wrote {resolved.stat().st_size} bytes to {path}"

    def _move_file(self, src: str, dst: str, overwrite: bool = False) -> str:
        _, dst_resolved = self.sandbox.move(src, dst, overwrite=overwrite)
        return f"moved {src} -> {dst} ({dst_resolved.stat().st_size} bytes)"
