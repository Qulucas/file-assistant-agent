from __future__ import annotations

import os
from pathlib import Path


class SandboxViolation(Exception):
    pass


class ToolError(Exception):
    pass


class WorkspaceSandbox:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()

    def resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            candidate = p
        else:
            candidate = self.root / p
        resolved = candidate.resolve(strict=False)
        if not self._contains(resolved):
            raise SandboxViolation(
                f"path escapes workspace root: {path!r} -> {resolved}"
            )
        return resolved

    def relative(self, abs_path: Path) -> str:
        return str(abs_path.relative_to(self.root))

    def _contains(self, resolved: Path) -> bool:
        return resolved == self.root or self.root in resolved.parents

    def is_dir(self, path: str) -> bool:
        return self.resolve(path).is_dir()

    def is_file(self, path: str) -> bool:
        return self.resolve(path).is_file()

    def list_dir(self, path: str) -> list[str]:
        target = self.resolve(path)
        if not target.is_dir():
            raise ToolError(f"not a directory: {path!r}")
        entries = sorted(os.listdir(target))
        return entries

    def walk_files(self, path: str = ".") -> list[str]:
        target = self.resolve(path)
        if not target.is_dir():
            raise ToolError(f"not a directory: {path!r}")
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames.sort()
            for f in sorted(filenames):
                abs_path = Path(dirpath) / f
                out.append(self.relative(abs_path))
        return out

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        resolved = self.resolve(path)
        try:
            return resolved.read_text(encoding=encoding)
        except UnicodeDecodeError:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"cannot read file {path!r}: {exc}")

    def write_text(self, path: str, content: str) -> Path:
        resolved = self.resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return resolved

    def move(self, src: str, dst: str, overwrite: bool = False) -> tuple[Path, Path]:
        src_resolved = self.resolve(src)
        dst_resolved = self.resolve(dst)
        if not src_resolved.exists():
            raise ToolError(f"source not found: {src!r}")
        if dst_resolved.exists() and not overwrite:
            raise ToolError(f"destination already exists: {dst!r}")
        dst_resolved.parent.mkdir(parents=True, exist_ok=True)
        src_resolved.rename(dst_resolved)
        return src_resolved, dst_resolved

    def file_size(self, path: str) -> int:
        return self.resolve(path).stat().st_size
