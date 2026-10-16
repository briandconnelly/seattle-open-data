"""Summarize eval transcripts: tool calls, error codes, cost, and the final answer.

Usage: python evals/report.py <out_dir> [--answers]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "mcp__seattle-open-data__"


def summarize(path: Path) -> dict:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    calls, results = [], {}
    for event in events:
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append(
                    (block["id"], block["name"].removeprefix(PREFIX), block.get("input", {}))
                )
            elif block.get("type") == "tool_result":
                text = block.get("content")
                if isinstance(text, list):
                    text = " ".join(part.get("text", "") for part in text if isinstance(part, dict))
                code = None
                if block.get("is_error"):
                    try:
                        code = json.loads(text).get("code")
                    except (TypeError, ValueError):
                        code = "error"
                results[block.get("tool_use_id")] = code
    if any(e.get("type") == "turn.completed" for e in events):
        return _summarize_codex(events)
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    return {
        "calls": [(name, results.get(call_id), args) for call_id, name, args in calls],
        "cost": final.get("total_cost_usd"),
        "turns": final.get("num_turns"),
        "answer": final.get("result", ""),
    }


def _summarize_codex(events: list[dict]) -> dict:
    """Codex `exec --json`: tool calls are mcp_tool_call items; usage is tokens, not dollars."""
    items = [e["item"] for e in events if e.get("type") == "item.completed" and e.get("item")]
    calls = [
        (str(item.get("tool")), _codex_error_code(item), item.get("arguments") or {})
        for item in items
        if item.get("type") == "mcp_tool_call"
    ]
    messages = [item.get("text", "") for item in items if item.get("type") == "agent_message"]
    return {
        "calls": calls,
        "cost": None,
        "turns": sum(1 for e in events if e.get("type") == "turn.completed"),
        "answer": messages[-1] if messages else "",
    }


def _codex_error_code(item: dict) -> str | None:
    if item.get("error"):
        return "error"
    result = item.get("result") or {}
    content = result.get("content") or [{}]
    try:
        payload = json.loads(content[0].get("text", ""))
    except (TypeError, ValueError):
        payload = {}
    # Codex may drop isError; an envelope is recognizable by code + temporary.
    is_envelope = isinstance(payload, dict) and {"code", "temporary"} <= payload.keys()
    if result.get("isError") or result.get("is_error") or is_envelope:
        return payload.get("code") if isinstance(payload, dict) and payload.get("code") else "error"
    return None


def main() -> None:
    out = Path(sys.argv[1])
    show_answers = "--answers" in sys.argv
    tasks = json.loads((ROOT / "evals" / "tasks.json").read_text())
    total = 0.0
    for task in tasks:
        path = out / f"{task['id']}.transcript.jsonl"
        if not path.exists():
            continue
        s = summarize(path)
        total += s["cost"] or 0
        cost, calls = s["cost"] or 0, len(s["calls"])
        print(f"\n## {task['id']}  (${cost:.2f}, {s['turns']} turns, {calls} calls)")
        print(f"expect: {task['expect']}")
        for name, code, args in s["calls"]:
            print(f"  - {name}{' -> ' + code if code else ''}  {json.dumps(args)[:180]}")
        if show_answers:
            print("answer:\n" + s["answer"].strip()[:1800])
    print(f"\ntotal cost ${total:.2f}")


if __name__ == "__main__":
    main()
