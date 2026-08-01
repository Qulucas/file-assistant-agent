from __future__ import annotations

import asyncio
import hmac
import json
import os
import shutil
import threading
import time
import uuid
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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WORKSPACE_SOURCE = Path(os.environ.get("WORKSPACE_SOURCE", BASE_DIR / "workspace"))
DEMO_WORKSPACE = Path(os.environ.get("DEMO_WORKSPACE", BASE_DIR / "demo_workspace"))
DEMO_RUNS_DIR = Path(os.environ.get("DEMO_RUNS_DIR", BASE_DIR / "demo_runs"))

PASSWORD = os.environ.get("DEMO_PASSWORD", "")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", None)
MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
MAX_STEPS = int(os.environ.get("DEMO_MAX_STEPS", "60"))
CONTEXT_BUDGET = int(os.environ.get("DEMO_CONTEXT_BUDGET", "24000"))
TOKEN_BUDGET = int(os.environ.get("DEMO_TOKEN_BUDGET", "1000000"))
RATE_PER_MIN = int(os.environ.get("DEMO_RATE_PER_MIN", "6"))
MAX_RUNS = int(os.environ.get("DEMO_MAX_RUNS", "1"))
MAX_FILE_BYTES = int(os.environ.get("DEMO_MAX_FILE_BYTES", "200000"))

app = FastAPI(title="File Assistant Agent Demo")

_lock = threading.Lock()
_token_used = 0
_runs: dict[str, dict[str, Any]] = {}
_run_clock: deque[float] = deque()
_clock_lock = threading.Lock()


def _ensure_workspace() -> None:
    if not WORKSPACE_SOURCE.is_dir():
        raise RuntimeError(f"WORKSPACE_SOURCE is not a directory: {WORKSPACE_SOURCE}")
    if not DEMO_WORKSPACE.exists():
        shutil.copytree(WORKSPACE_SOURCE, DEMO_WORKSPACE, ignore=shutil.ignore_patterns(".DS_Store"))
    DEMO_RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _check_auth(token: str | None) -> None:
    if not PASSWORD:
        raise HTTPException(status_code=503, detail="DEMO_PASSWORD not configured")
    if not token or not hmac.compare_digest(token, PASSWORD):
        raise HTTPException(status_code=401, detail="missing or invalid demo token")


def _check_rate(ip: str) -> None:
    now = time.monotonic()
    with _clock_lock:
        while _run_clock and now - _run_clock[0] > 60:
            _run_clock.popleft()
        if len(_run_clock) >= RATE_PER_MIN:
            raise HTTPException(status_code=429, detail="rate limit exceeded, try again in a minute")
        _run_clock.append(now)


def _check_budget() -> None:
    global _token_used
    with _lock:
        if _token_used >= TOKEN_BUDGET:
            raise HTTPException(status_code=503, detail="demo token budget exhausted; contact owner")


def _charge_tokens(tokens: int) -> None:
    global _token_used
    with _lock:
        _token_used += tokens


@app.get("/api/health")
def health() -> dict[str, Any]:
    with _lock:
        active = sum(1 for r in _runs.values() if not r["done"])
    return {"ok": True, "active_runs": active, "token_used": _token_used}


def _tree(path: Path, base: Path) -> dict[str, Any]:
    entry = {"name": path.name or base.name, "type": "dir", "children": []}
    for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            entry["children"].append(_tree(child, base))
        else:
            entry["children"].append({"name": child.name, "type": "file", "size": child.stat().st_size})
    return entry


@app.get("/api/tree")
def api_tree(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    return _tree(DEMO_WORKSPACE, DEMO_WORKSPACE)


@app.get("/api/file")
def api_file(path: str, x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    sandbox = WorkspaceSandbox(DEMO_WORKSPACE)
    try:
        resolved = sandbox.resolve(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if resolved.is_dir() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="not a file")
    size = resolved.stat().st_size
    truncated = size > MAX_FILE_BYTES
    data = resolved.read_bytes()[:MAX_FILE_BYTES]
    text = data.decode("utf-8", errors="replace")
    return {"path": path, "size": size, "truncated": truncated, "content": text}


@app.post("/api/reset")
def api_reset(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    with _lock:
        if any(not r["done"] for r in _runs.values()):
            raise HTTPException(status_code=409, detail="a run is active; wait for it to finish")
    shutil.rmtree(DEMO_WORKSPACE, ignore_errors=True)
    shutil.copytree(WORKSPACE_SOURCE, DEMO_WORKSPACE, ignore=shutil.ignore_patterns(".DS_Store"))
    return {"ok": True, "message": f"workspace reset to {len(list(DEMO_WORKSPACE.rglob('*')))} entries"}


def _emit(run_id: str, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
    run = _runs.get(run_id)
    if run:
        loop.call_soon_threadsafe(run["queue"].put_nowait, event)


def _worker(run_id: str, task: str, loop: asyncio.AbstractEventLoop) -> None:
    trace = TraceLogger(str(DEMO_RUNS_DIR / f"{run_id}.jsonl"))
    sandbox = WorkspaceSandbox(DEMO_WORKSPACE)
    registry = ToolRegistry(sandbox)
    llm = LLMClient(model=MODEL, api_key=API_KEY, base_url=BASE_URL)
    agent = AgentLoop(
        llm=llm,
        registry=registry,
        context=ContextManager(budget=CONTEXT_BUDGET),
        trace=trace,
        max_steps=MAX_STEPS,
        on_step=lambda ev: _emit(run_id, loop, ev),
    )
    try:
        result = agent.run([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ])
        _charge_tokens(result["prompt_tokens"] + result["completion_tokens"])
        _emit(run_id, loop, {"type": "result", **result})
    except Exception as exc:
        _emit(run_id, loop, {"type": "error", "detail": f"{type(exc).__name__}: {exc}"})
    finally:
        run = _runs.get(run_id)
        if run:
            run["done"] = True


@app.post("/api/runs")
async def api_runs(request: Request, x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_demo_token)
    if not API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured on server")
    body = await request.json()
    task = str(body.get("task", "")).strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is empty")
    ip = request.client.host if request.client else "unknown"
    _check_rate(ip)
    _check_budget()
    with _lock:
        if sum(1 for r in _runs.values() if not r["done"]) >= MAX_RUNS:
            raise HTTPException(status_code=409, detail="a run is already in progress; wait or reset")

    run_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    _runs[run_id] = {
        "queue": asyncio.Queue(),
        "done": False,
        "created": time.time(),
        "client_ip": ip,
        "task": task,
    }
    thread = threading.Thread(target=_worker, args=(run_id, task, loop), daemon=True)
    thread.start()
    return {"run_id": run_id}


async def _event_stream(run_id: str):
    run = _runs.get(run_id)
    if not run:
        yield "data: {\"type\": \"error\", \"detail\": \"unknown run\"}\n\n"
        return
    queue: asyncio.Queue = run["queue"]
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=60)
        except asyncio.TimeoutError:
            yield "data: {\"type\": \"ping\"}\n\n"
            continue
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if event.get("type") in ("result", "error"):
            break


@app.get("/api/runs/{run_id}/events")
async def api_run_events(run_id: str, x_demo_token: str | None = Header(default=None)) -> StreamingResponse:
    _check_auth(x_demo_token)
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="unknown run")
    return StreamingResponse(
        _event_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    _ensure_workspace()
    if not PASSWORD:
        print("WARNING: DEMO_PASSWORD not set; run with DEMO_PASSWORD=... ", flush=True)
    port = int(os.environ.get("DEMO_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
