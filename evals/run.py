"""Run eval tasks headless in Claude Code or Codex against the capture server.

Usage: python evals/run.py [--host claude|codex] <out_dir> [task_id ...]
Writes <id>.transcript.jsonl (the host's JSON event stream), <id>.capture.jsonl (server view),
and <id>.stderr.txt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ",".join(
    f"mcp__seattle-open-data__seattle_data_{name}"
    for name in ("search_datasets", "describe_dataset", "query_dataset", "get_reference")
)


def run(task: dict, out: Path, host: str) -> None:
    capture = out / f"{task['id']}.capture.jsonl"
    capture.unlink(missing_ok=True)
    args = [
        "run",
        "--directory",
        str(ROOT),
        "python",
        "scripts/capture_server.py",
        str(capture),
    ]
    command = codex_command(task, args) if host == "codex" else claude_command(task, args, out)
    with (
        (out / f"{task['id']}.transcript.jsonl").open("w") as stdout,
        (out / f"{task['id']}.stderr.txt").open("w") as stderr,
    ):
        code = subprocess.call(command, cwd=out, stdout=stdout, stderr=stderr, env=os.environ)
    print(f"{task['id']}: exit {code}", flush=True)


def claude_command(task: dict, args: list[str], out: Path) -> list[str]:
    config = out / f"{task['id']}.mcp.json"
    server = {"command": "uv", "args": args, "env": task.get("env", {})}
    config.write_text(json.dumps({"mcpServers": {"seattle-open-data": server}}))
    return [
        "claude",
        "-p",
        task["prompt"],
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        "--allowedTools",
        TOOLS,
        "--max-turns",
        "20",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def codex_command(task: dict, args: list[str]) -> list[str]:
    # Codex takes MCP servers as TOML config overrides; JSON strings and arrays are valid TOML.
    overrides = [
        'mcp_servers.seattle_open_data.command="uv"',
        f"mcp_servers.seattle_open_data.args={json.dumps(args)}",
    ]
    for key, value in task.get("env", {}).items():
        overrides.append(f"mcp_servers.seattle_open_data.env.{key}={json.dumps(value)}")
    command = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-c",
        'approval_policy="never"',
    ]
    for override in overrides:
        command += ["-c", override]
    return [*command, "--json", task["prompt"]]


def main() -> None:
    argv = sys.argv[1:]
    host = "claude"
    if argv[:1] == ["--host"]:
        host, argv = argv[1], argv[2:]
    if host not in ("claude", "codex"):
        sys.exit(f"unknown host {host!r}; use claude or codex")
    out = Path(argv[0]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(argv[1:])
    for task in json.loads((ROOT / "evals" / "tasks.json").read_text()):
        if not wanted or task["id"] in wanted:
            run(task, out, host)


if __name__ == "__main__":
    main()
