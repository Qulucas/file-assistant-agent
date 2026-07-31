from __future__ import annotations

import argparse
import os
import sys

from falcon_agent import AgentLoop, ContextManager, LLMClient, ToolRegistry, TraceLogger, WorkspaceSandbox


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="File assistant agent over a workspace sandbox")
    p.add_argument("--workspace", required=True, help="path to the workspace directory")
    p.add_argument("--task", required=True, help="natural-language task for the agent")
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "deepseek-chat"))
    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", None))
    p.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    p.add_argument("--steps", type=int, default=30, help="max tool steps (default 30)")
    p.add_argument("--trace", default="trace.jsonl", help="trace output file (jsonl)")
    p.add_argument("--context-budget", type=int, default=24000, help="approx token budget for the LLM context window")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        print("error: no API key. Set OPENAI_API_KEY or pass --api-key.", file=sys.stderr)
        return 2

    sandbox = WorkspaceSandbox(args.workspace)
    registry = ToolRegistry(sandbox)
    llm = LLMClient(model=args.model, api_key=args.api_key, base_url=args.base_url)
    context = ContextManager(budget=args.context_budget)
    trace = TraceLogger(args.trace)
    loop = AgentLoop(llm=llm, registry=registry, context=context, trace=trace, max_steps=args.steps)

    result = loop.run(args.task)
    print(result["final"])
    print(
        f"\n[trace] {args.trace}: {result['steps']} tool steps, "
        f"{result['llm_calls']} LLM calls, "
        f"{result['prompt_tokens']} prompt + {result['completion_tokens']} completion tokens "
        f"(stopped: {result['stopped_reason']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
