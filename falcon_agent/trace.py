from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else None
        self.step_count = 0
        self.rows: list[dict[str, Any]] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def tool_step(self, tool: str, args: dict[str, Any], result: str) -> None:
        self.step_count += 1
        row = {
            "step": self.step_count,
            "tool": tool,
            "args": args,
            "result_summary": self._summary(result),
        }
        self.rows.append(row)
        self._append(row)

    def final(self, **stats: Any) -> None:
        row = {"final": True, **stats}
        self.rows.append(row)
        self._append(row)

    def _summary(self, result: str) -> str:
        head = result.split("\n")[0]
        if len(head) > 300:
            head = head[:300] + "..."
        return head

    def _append(self, row: dict[str, Any]) -> None:
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
