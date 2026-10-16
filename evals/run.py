"""Run eval tasks headless in Claude Code against the capture server.

Usage: python evals/run.py <out_dir> [task_id ...]
Writes <id>.transcript.jsonl (stream-json), <id>.capture.jsonl (server view), <id>.stderr.txt.
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


def run(task: dict, out: Path) -> None:
    capture = out / f"{task['id']}.capture.jsonl"
    capture.unlink(missing_ok=True)
    config = out / f"{task['id']}.mcp.json"
    server = {
        "command": "uv",
        "args": [
            "run",
            "--directory",
            str(ROOT),
            "python",
            "scripts/capture_server.py",
            str(capture),
        ],
        "env": task.get("env", {}),
    }
    config.write_text(json.dumps({"mcpServers": {"seattle-open-data": server}}))
    with (
        (out / f"{task['id']}.transcript.jsonl").open("w") as stdout,
        (out / f"{task['id']}.stderr.txt").open("w") as stderr,
    ):
        code = subprocess.call(
            [
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
            ],
            cwd=out,
            stdout=stdout,
            stderr=stderr,
            env=os.environ,
        )
    print(f"{task['id']}: exit {code}", flush=True)


def main() -> None:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(sys.argv[2:])
    for task in json.loads((ROOT / "evals" / "tasks.json").read_text()):
        if not wanted or task["id"] in wanted:
            run(task, out)


if __name__ == "__main__":
    main()
