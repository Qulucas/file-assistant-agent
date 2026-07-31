import json

from falcon_agent.trace import TraceLogger


def test_trace_append_and_read_back(tmp_path):
    path = tmp_path / "trace.jsonl"
    t = TraceLogger(str(path))
    t.tool_step("list_dir", {"path": "."}, "5 entries\n- a\n- b")
    t.tool_step("search", {"pattern": "Falcon"}, "3 matches across 2 files")
    t.final(stopped_reason="completed", llm_calls=3, prompt_tokens=100, completion_tokens=20)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["step"] == 1
    assert first["tool"] == "list_dir"
    assert first["args"] == {"path": "."}
    assert first["result_summary"] == "5 entries"
    second = json.loads(lines[1])
    assert second["step"] == 2
    last = json.loads(lines[2])
    assert last["final"] is True
    assert last["llm_calls"] == 3


def test_trace_result_summary_truncated(tmp_path):
    t = TraceLogger(str(tmp_path / "t.jsonl"))
    t.tool_step("read_file", {"path": "x"}, "a" * 500 + "\ntail")
    row = json.loads((tmp_path / "t.jsonl").read_text().strip().split("\n")[0])
    assert len(row["result_summary"]) <= 303
    assert row["result_summary"].endswith("...")


def test_trace_no_file_when_path_none(tmp_path):
    t = TraceLogger()
    t.tool_step("search", {"pattern": "x"}, "ok")
    t.final()
    assert not list(tmp_path.iterdir())
    assert len(t.rows) == 2


def test_trace_creates_parent_dirs(tmp_path):
    path = tmp_path / "deep" / "nested" / "trace.jsonl"
    TraceLogger(str(path)).tool_step("write_file", {"path": "a"}, "ok")
    assert path.exists()
