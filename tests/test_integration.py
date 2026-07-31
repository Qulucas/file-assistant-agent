import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

T1_TASK = (
    '找出 workspace 里所有提到 "Project Falcon" 的文件,在 workspace 根目录生成 '
    "falcon_index.md:开头写明该项目当前的正式名称;正文按月份分组("
    "## YYYY-MM 标题,月份取文件自身标注的日期),每个文件一行:"
    "- <相对路径> — <一句话摘要>。"
)
T2_TASK = (
    "把 drafts/ 里所有内容标记为 status: obsolete 的草稿移动到 archive/"
    "(不存在则创建),并生成 archive/MANIFEST.md,每行 - <文件名> 登记被移动的文件。"
    "除此之外的任何文件都不许动。"
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def live_workspace(tmp_path: Path) -> Path:
    src = REPO_ROOT / "workspace"
    dst = tmp_path / "ws"
    shutil.copytree(src, dst)
    return dst


def run_cli(workspace: Path, task: str, trace_name: str, tmp_path: Path):
    env = os.environ.copy()
    env.setdefault("OPENAI_API_KEY", "")
    env.setdefault("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    env.setdefault("OPENAI_MODEL", "deepseek-chat")
    trace = tmp_path / trace_name
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "agent.py"),
            "--workspace",
            str(workspace),
            "--task",
            task,
            "--trace",
            str(trace),
            "--steps",
            "40",
        ],
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    return proc, trace


@pytest.mark.live
def test_t1_t2_end_to_end(live_workspace: Path, tmp_path: Path):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set; set it to run the live integration test")
    proc1, trace1 = run_cli(live_workspace, T1_TASK, "t1.jsonl", tmp_path)
    assert proc1.returncode == 0, proc1.stderr
    proc2, trace2 = run_cli(live_workspace, T2_TASK, "t2.jsonl", tmp_path)
    assert proc2.returncode == 0, proc2.stderr
    for trace in (trace1, trace2):
        lines = [json.loads(l) for l in trace.read_text().strip().splitlines()]
        assert any("final" in l for l in lines)
    verify = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_workspace.py"),
         "--workspace", str(live_workspace)],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


@pytest.mark.live
def test_trace_format_spec(live_workspace: Path, tmp_path: Path):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set; set it to run the live integration test")
    proc, trace = run_cli(live_workspace, T1_TASK, "t1b.jsonl", tmp_path)
    assert proc.returncode == 0
    for line in trace.read_text().strip().splitlines():
        row = json.loads(line)
        if "final" in row:
            continue
        assert set(row) == {"step", "tool", "args", "result_summary"}
        assert isinstance(row["step"], int)
        assert isinstance(row["args"], dict)
        assert isinstance(row["result_summary"], str)


def _write_expected_artifacts(ws: Path) -> None:
    index = ws / "falcon_index.md"
    index.write_text(
        "# 索引\n\n当前正式名称:**Project Phoenix**(自 2026-01-22)\n\n"
        "## 2025-09\n\n- meetings/2025-09-04-migration-standup.md — 站会确认采样率 5%。\n\n"
        "## 2025-10\n\n- data/2025-10-vendor-tracking.csv — 供应商合同关联 Project Falcon。\n"
        "- meetings/2025-10-08-eng-sync.md — 风险评审,schema drift 为首要项。\n"
        "- notes/falcon-migration-checklist.md — 迁移检查清单。\n\n"
        "## 2025-11\n\n- meetings/2025-11-13-data-review.md — 摄取管道已上新集群。\n"
        "- meetings/2025-11-14-steering.md — 切换演练需回滚计划。\n\n"
        "## 2025-12\n\n- logs/2025-12-full-export.log — 演练延迟后完成。\n"
        "- meetings/2025-12-07-platform-sync.md — 双写决策。\n\n"
        "## 2026-01\n\n- meetings/2026-01-14-cutover-planning.md — 预算获批。\n"
        "- meetings/2026-01-22-all-hands.md — 更名为 Project Phoenix。\n",
        encoding="utf-8",
    )
    archive = ws / "archive"
    archive.mkdir()
    for name in ("blog-post-launch.md", "onboarding-guide.md", "api-v1-spec.md"):
        (ws / "drafts" / name).rename(archive / name)
    (archive / "MANIFEST.md").write_text(
        "- api-v1-spec.md\n- blog-post-launch.md\n- onboarding-guide.md\n",
        encoding="utf-8",
    )


def test_verify_script_accepts_good_artifacts(tmp_path: Path):
    ws = tmp_path / "ws"
    shutil.copytree(REPO_ROOT / "workspace", ws)
    _write_expected_artifacts(ws)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_workspace.py"),
         "--workspace", str(ws)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout


def test_verify_script_rejects_missing_archive(tmp_path: Path):
    ws = tmp_path / "ws"
    shutil.copytree(REPO_ROOT / "workspace", ws)
    (ws / "falcon_index.md").write_text("# X\n\n当前正式名称:**Project Phoenix**\n")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_workspace.py"),
         "--workspace", str(ws)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "archive" in proc.stdout


def test_verify_script_rejects_wrong_name(tmp_path: Path):
    ws = tmp_path / "ws"
    shutil.copytree(REPO_ROOT / "workspace", ws)
    _write_expected_artifacts(ws)
    (ws / "falcon_index.md").write_text(
        (ws / "falcon_index.md").read_text().replace("Project Phoenix", "Project Falcon"),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_workspace.py"),
         "--workspace", str(ws)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "Project Phoenix" in proc.stdout
