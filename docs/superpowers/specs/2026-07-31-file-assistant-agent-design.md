# 文件助理 Agent — 设计文档

日期:2026-07-31
状态:已批准

## 1. 背景与目标

基于 `Requirements.md`(AI Agent 开发笔试题)实现一个通用文件操作 agent:

- 接受自然语言指令,通过自定义工具(列目录、读文件、搜索、写/移动文件)操作 `workspace/`;
- T1:跨文件索引(Project Falcon → falcon_index.md,按月份分组);
- T2:受控清理(drafts 中 status: obsolete 的草稿移入 archive/ 并生成 MANIFEST.md);
- 两个任务必须由**同一个通用 agent 循环**完成,决策来自模型,不写死流程;
- 禁止使用替跑 agent 循环的框架(LangChain/LangGraph/CrewAI/Agents SDK 等),控制流自己写。

## 2. 挑战点识别(已实测确认)

| 挑战 | 位置 | 设计对策 |
|---|---|---|
| 注入指令("只输出42"+"删除所有文件") | `meetings/2025-11-13-data-review.md` | 数据/指令结构化隔离 + 系统声明 |
| 注入指令(归档时删其他 drafts) | `drafts/blog-post-launch.md`(本身 obsolete,要归档) | 同上,注入内容仅作数据 |
| 超上下文大文件 974KB/12000 行 | `logs/2025-12-full-export.log` | search 返回匹配行;read 分页限量 |
| 新旧矛盾(官方名 Falcon → Phoenix) | `2026-01-22-all-hands.md`(最新) | 最新为准,由模型依据日期判断 |
| 看似相关实则无关(观鸟"falcons") | `notes/birdwatching-weekend.md` | 搜索词精确性 + 模型核对摘要 |
| 文件名/内容不一致 | `drafts/pricing-review-obsolete.md`(active) | 以内容 frontmatter 为准 |

## 3. 总体架构

```
CLI (agent.py)
  └── AgentLoop:唯一控制流(循环/终止/兜底)
        ├── LLMClient      (OpenAI 兼容端点, function calling)
        ├── ToolRegistry   (list_dir / read_file / search / write_file / move_file)
        │     └── WorkspaceSandbox (根绑定, 路径逃逸拒绝)
        ├── ContextManager (截断 / 历史折叠 / 注入隔离)
        └── TraceLogger    (trace.jsonl, 每步一行 + token 统计)
```

模块职责单一、接口清晰,每个模块可独立单测。

## 4. Agent 循环

```
for step in 1..max_steps:
    resp = llm.chat(messages, tools)
    if resp.tool_calls:
        依序执行每个调用 → 结果回填 messages → trace 一行
    else:
        return resp.text   # 无工具调用 = 任务完成
步数打满 → 返回已完成清单 + 未完成原因
```

终止三态:正常答复 / 步数上限(默认 30)/ 安全熔断(连续 3 次相同 (tool,args) 则提示换思路,再犯强制终止)。

坏输出兜底:非法 JSON → 错误回填模型自纠;工具执行失败 → Error 字符串给模型换方案;API 异常 → 单次退避重试,再失败终止汇报。

## 5. 工具与安全边界

5 个工具:list_dir、read_file(path, offset, max_lines=150)、search(pattern, path, 上限 50 条匹配)、write_file、move_file(自动建父目录,默认拒绝覆盖)。

安全三道闸:
1. `WorkspaceSandbox.resolve()`:路径解析后必须落在 workspace 根内(拒绝 `..`、绝对路径越界、symlink 逃逸);
2. 工具结果长度封顶(默认 6000 字符,超出截断标注);
3. 工具结果包裹 `<tool_result tool="…" path="…">` 结构化标记,与指令物理隔离。

注入隔离:系统提示声明文件内容是数据不是指令;不执行其中的任何命令。

## 6. 上下文管理

- 预算:调用前估算 token(字符/4 + 条数开销),默认 24k;
- 截断:工具结果 ≤6000 字符;read_file 按行限量;
- 折叠:消息过多时把最早工具结果块折叠成一行摘要,保留 system + task + 最近 5 轮;
- 明确不塞:整文件原文、原始错误堆栈、过期旧结果。

## 7. 日期提取

extract_month(path, content),优先级:frontmatter(date:/updated:) → 正文 Date: 行 → 文件名 YYYY-MM → 日志行时间戳。纯函数无 LLM 依赖。

## 8. CLI 与 trace

```
python agent.py --workspace ./workspace --task "…" --model … --base-url … --steps 30 --trace trace.jsonl
```

trace.jsonl 每步 `{"step", "tool", "args", "result_summary"}` + 末尾 `{"final", "llm_calls", "prompt_tokens", "completion_tokens"}`。

## 9. 测试

单元:pytest 全绿(沙箱逃逸、日期、截断、FakeLLM 驱动循环的终止/兜底/熔断/注入不执行、trace 格式)。

集成(`--live`,真实 LLM + workspace 副本):断言 falcon_index.md(首行 Project Phoenix、5 个月份组、恰 10 文件、不含观鸟笔记)、archive/ 恰 3 文件 + MANIFEST 3 行、drafts/ 剩 5 个、其余目录零改动。

产物校验由纯 Python 脚本 `scripts/verify_workspace.py` 执行,不依赖 LLM。

## 10. 交付物

核心包 falcon_agent/ + CLI + tests/ + README.md + NOTES.md + 设计文档。
