from __future__ import annotations

import asyncio
import hmac
import json
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from falcon_agent import AgentLoop, ContextManager, LLMClient, ToolRegistry, TraceLogger, WorkspaceSandbox
from falcon_agent.agent import SYSTEM_PROMPT
from demo_storage import LockTimeout, StorageError, make_storage, seed_files

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WORKSPACE_SOURCE = Path(os.environ.get("WORKSPACE_SOURCE", BASE_DIR / "workspace"))

PASSWORD = os.environ.get("DEMO_PASSWORD", "")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", None)
MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
MAX_STEPS = int(os.environ.get("DEMO_MAX_STEPS", "60"))
CONTEXT_BUDGET = int(os.environ.get("DEMO_CONTEXT_BUDGET", "24000"))
TOKEN_BUDGET = int(os.environ.get("DEMO_TOKEN_BUDGET", "1000000"))
RATE_PER_MIN = int(os.environ.get("DEMO_RATE_PER_MIN", "6"))
MAX_FILE_BYTES = int(os.environ.get("DEMO_MAX_FILE_BYTES", "200000"))

STORAGE_KIND = os.environ.get("DEMO_STORAGE", "blob" if os.environ.get("BLOB_READ_WRITE_TOKEN") else "local")

app = FastAPI(title="File Assistant Agent Demo")

_lock = asyncio.Lock()
_token_used = 0
_run_clock: deque[float] = deque()
_clock_lock = asyncio.Lock()
_storage: Any = None


def _get_storage():
    global _storage
    if _storage is None:
        token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        _storage = make_storage(STORAGE_KIND, BASE_DIR, token)
    return _storage


def _check_auth(x_demo_token: str | None) -> None:
    if not PASSWORD:
        raise HTTPException(status_code=503, detail="DEMO_PASSWORD not configured")
    if not x_demo_token or not hmac.compare_digest(x_demo_token, PASSWORD):
        raise HTTPException(status_code=401, detail="missing or invalid demo token")


async def _check_rate(ip: str) -> None:
    if STORAGE_KIND == "blob":
        return
    now = time.monotonic()
    async with _clock_lock:
        while _run_clock and now - _run_clock[0] > 60:
            _run_clock.popleft()
        if len(_run_clock) >= RATE_PER_MIN:
            raise HTTPException(status_code=429, detail="rate limit exceeded, try again in a minute")
        _run_clock.append(now)


async def _check_budget() -> None:
    global _token_used
    async with _lock:
        if _token_used >= TOKEN_BUDGET:
            raise HTTPException(status_code=503, detail="demo token budget exhausted; contact owner")


async def _charge_tokens(tokens: int) -> None:
    global _token_used
    async with _lock:
        _token_used += tokens


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "active_runs": False, "token_used": _token_used, "storage": STORAGE_KIND}


@app.get("/api/tree")
async def api_tree(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    _get_storage().seed(seed_files(WORKSPACE_SOURCE))
    files = _get_storage().list_files()
    return {"name": "workspace", "type": "dir", "children": _files_to_tree(files)}


def _files_to_tree(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root: dict[str, dict[str, Any]] = {"children": {}}
    for f in files:
        parts = f["path"].split("/")
        node = root
        for part in parts[:-1]:
            node = node["children"].setdefault(part, {"dir": True, "children": {}})
        node["children"][parts[-1]] = {"dir": False, "size": f["size"]}
    return _render(root["children"])


def _render(node: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for name, val in sorted(node.items()):
        if val.get("dir"):
            out.append({"name": name, "type": "dir", "children": _render(val["children"])})
        else:
            out.append({"name": name, "type": "file", "size": val["size"]})
    return out


@app.get("/api/file")
async def api_file(path: str, x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    rel = _sanitize(path)
    _get_storage().seed(seed_files(WORKSPACE_SOURCE))
    try:
        data = _get_storage().read(rel)
    except (StorageError, FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="not a file")
    except Exception:
        raise HTTPException(status_code=500, detail="failed to read file")
    size = len(data)
    truncated = size > MAX_FILE_BYTES
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return {"path": path, "size": size, "truncated": truncated, "content": text}


def _sanitize(path: str) -> str:
    p = path.replace("\\", "/").lstrip("/")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise HTTPException(status_code=400, detail=f"path escapes workspace root: {path!r}")
    return "/".join(parts)


@app.post("/api/reset")
async def api_reset(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    storage = _get_storage()
    try:
        storage.acquire_lock(timeout=2.0)
    except LockTimeout:
        raise HTTPException(status_code=409, detail="a run is active; wait for it to finish")
    try:
        storage.reset(seed_files(WORKSPACE_SOURCE))
    finally:
        storage.release_lock()
    return {"ok": True, "message": f"workspace reset to {len(storage.list_files())} files"}


@app.post("/api/runs")
async def api_runs(request: Request, x_demo_token: str | None = Header(default=None)) -> StreamingResponse:
    _check_auth(x_demo_token)
    if not API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured on server")
    body = await request.json()
    task = str(body.get("task", "")).strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is empty")
    ip = request.client.host if request.client else "unknown"
    await _check_rate(ip)
    await _check_budget()

    return StreamingResponse(
        _run_stream(task),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"},
    )


def _emit(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _run_stream(task: str):
    storage = _get_storage()
    try:
        storage.acquire_lock(timeout=5.0)
    except LockTimeout:
        yield _emit({"type": "error", "detail": "another run is in progress; wait a minute and retry"})
        return
    yield _emit({"type": "status", "message": "workspace ready, starting agent"})

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_step(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("step", event))

    def worker() -> tuple[str, dict[str, Any]]:
        try:
            storage.seed(seed_files(WORKSPACE_SOURCE))
            if STORAGE_KIND == "blob":
                tmp = Path(tempfile.mkdtemp(prefix="demo-ws-"))
                try:
                    storage.materialize(tmp)
                    result = _run_agent(tmp, task, on_step)
                    try:
                        storage.sync_from(tmp)
                    except Exception as exc:
                        result = {**result, "sync_warning": f"{type(exc).__name__}: {exc}"}
                finally:
                    import shutil

                    shutil.rmtree(tmp, ignore_errors=True)
            else:
                result = _run_agent(BASE_DIR / "demo_workspace", task, on_step)
            return ("result", result)
        except Exception as exc:
            return ("error", {"type": "error", "detail": f"{type(exc).__name__}: {exc}"})

    async def drive() -> None:
        outcome = await asyncio.to_thread(worker)
        queue.put_nowait(outcome)

    drive = asyncio.create_task(drive())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=60)
            except asyncio.TimeoutError:
                yield _emit({"type": "ping"})
                continue
            if kind == "error":
                yield _emit(payload)
                break
            if kind == "result":
                await _charge_tokens(payload.get("prompt_tokens", 0) + payload.get("completion_tokens", 0))
                yield _emit({"type": "result", **payload})
                break
            yield _emit(payload)
    finally:
        drive.cancel()
        try:
            await drive
        except (asyncio.CancelledError, Exception):
            pass
        try:
            storage.release_lock()
        except Exception:
            pass


def _run_agent(workdir: Path, task: str, on_step) -> dict[str, Any]:
    trace = TraceLogger()
    sandbox = WorkspaceSandbox(workdir)
    registry = ToolRegistry(sandbox)
    llm = LLMClient(model=MODEL, api_key=API_KEY, base_url=BASE_URL)
    agent = AgentLoop(
        llm=llm,
        registry=registry,
        context=ContextManager(budget=CONTEXT_BUDGET),
        trace=trace,
        max_steps=MAX_STEPS,
        on_step=on_step,
    )
    return agent.run(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    if not PASSWORD:
        print("WARNING: DEMO_PASSWORD not set; run with DEMO_PASSWORD=... ", flush=True)
    port = int(os.environ.get("DEMO_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
