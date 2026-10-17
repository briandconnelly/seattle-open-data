"""Start the server from a built artifact, the way a user would, and check it answers.

- `--wheel PATH`: install the wheel into a fresh venv and run its console script.
- `--sdist PATH`: the same from the sdist, which builds it first, as an installer without a
  matching wheel would.
- `--bundle PATH`: unpack the .mcpb and run the command its manifest gives the host, with the
  app token left as the unexpanded placeholder a host passes when the user leaves it blank.

Either way the server must complete the handshake, list exactly the tools in
`tests/snapshots/tools_list.json`, and serve the reference index. None of that touches the network.

Usage: uv run python scripts/smoke_test.py (--wheel dist/*.whl | --sdist dist/*.tar.gz |
       --bundle dist/*.mcpb)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "snapshots" / "tools_list.json"


def expected_tools() -> list[str]:
    return sorted(tool["name"] for tool in json.loads(SNAPSHOT.read_text())["tools"])


def package_command(package: Path, work: Path) -> tuple[str, list[str], dict[str, str]]:
    venv = work / "venv"
    subprocess.run(["uv", "venv", "--quiet", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(python), str(package)], check=True
    )
    script = venv / (
        "Scripts/seattle-open-data.exe" if os.name == "nt" else "bin/seattle-open-data"
    )
    return str(script), [], {}


def bundle_command(bundle: Path, work: Path) -> tuple[str, list[str], dict[str, str]]:
    unpacked = work / "bundle"
    zipfile.ZipFile(bundle).extractall(unpacked)
    config = json.loads((unpacked / "manifest.json").read_text())["server"]["mcp_config"]
    args = [arg.replace("${__dirname}", str(unpacked)) for arg in config["args"]]
    return config["command"], args, dict(config.get("env", {}))


async def check(command: str, args: list[str], env: dict[str, str]) -> list[str]:
    transport = StdioTransport(command=command, args=args, env={**os.environ, **env})
    async with Client(transport) as client:
        problems = []
        tools = sorted(tool.name for tool in await client.list_tools())
        if tools != expected_tools():
            problems.append(f"tools {tools} != snapshot {expected_tools()}")
        index = await client.call_tool("seattle_data_get_reference", {}, raise_on_error=False)
        if index.is_error or not index.structured_content:
            problems.append(f"reference index failed: {index.content}")
        return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kind = parser.add_mutually_exclusive_group(required=True)
    kind.add_argument("--wheel", type=Path)
    kind.add_argument("--sdist", type=Path)
    kind.add_argument("--bundle", type=Path)
    opts = parser.parse_args()
    artifact: Path = opts.wheel or opts.sdist or opts.bundle
    with tempfile.TemporaryDirectory() as tmp:
        build = bundle_command if opts.bundle else package_command
        command, args, env = build(artifact.resolve(), Path(tmp))
        problems = asyncio.run(check(command, args, env))
    if problems:
        print(f"{artifact.name}: FAILED")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"{artifact.name}: server started, {len(expected_tools())} tools, reference index served")
    return 0


if __name__ == "__main__":
    sys.exit(main())
