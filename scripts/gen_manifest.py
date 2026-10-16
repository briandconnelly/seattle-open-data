#!/usr/bin/env python
"""Generate ``manifest.json`` (the MCPB bundle manifest) from a single source
of truth.

Identity (name/version/description/author/keywords/python-requirement) comes
from ``pyproject.toml``; the tool list is derived from the *live* FastMCP
server so the manifest cannot drift from what the server actually exposes.
Only the bundle-specific surface (display name, docs URL, launch command, and
the ``user_config`` knobs) lives here as constants.

``tests/test_manifest.py`` asserts the committed ``manifest.json`` matches this
generator's output, so regenerate and commit whenever the version or tool
surface changes.

Usage:
    uv run python scripts/gen_manifest.py            # write manifest.json
    uv run python scripts/gen_manifest.py --check     # exit 1 if stale
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
MANIFEST = REPO_ROOT / "manifest.json"

# --- Bundle-specific surface (the only per-server knobs to edit) ------------

DISPLAY_NAME = "Seattle Open Data"

# The installed bundle launches the stdio server from its own uv.lock; the end
# user only needs `uv` on PATH.
LAUNCH_ARGS = [
    "run",
    "--directory",
    "${__dirname}",
    "--frozen",
    "--no-dev",
    "seattle-open-data",
]

USER_CONFIG = {
    "app_token": {
        "type": "string",
        "title": "Socrata app token (optional)",
        "description": "A Socrata application token raises rate limits. Leave blank to "
        "query anonymously. A rejected token is reported, never silently dropped.",
        "sensitive": True,
        "required": False,
    },
}

ENV_FROM_CONFIG = {
    "SOCRATA_APP_TOKEN": "${user_config.app_token}",
}

# ---------------------------------------------------------------------------


def _tools() -> list[dict[str, str]]:
    """Manifest ``tools`` entries, derived from the live server so the
    name/description pairs stay in lockstep with what agents actually see."""
    from seattle_open_data.mcp.server import build_server  # noqa: PLC0415

    async def _extract() -> list[dict[str, str]]:
        mcp = build_server()
        out: list[dict[str, str]] = []
        for tool in await mcp.list_tools():
            mcp_tool = tool.to_mcp_tool()
            out.append(
                {
                    "name": mcp_tool.name,
                    "description": " ".join(
                        (mcp_tool.description or "").strip().split("\n\n", 1)[0].split()
                    ),
                }
            )
        return out

    return asyncio.run(_extract())


def build_manifest() -> dict:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    author = project["authors"][0]
    urls = project.get("urls", {})
    repo_url = urls.get("Repository") or urls.get("Homepage", "")

    manifest: dict = {
        "manifest_version": "0.4",
        "name": project["name"],
        "display_name": DISPLAY_NAME,
        "version": project["version"],
        "description": project["description"],
        "author": {"name": author["name"], "email": author["email"]},
    }
    # URLs appear only once pyproject declares them; never point at a repo that may not exist.
    if repo_url:
        manifest["repository"] = {"type": "git", "url": repo_url}
        manifest["homepage"] = urls.get("Homepage", repo_url)
        manifest["documentation"] = f"{repo_url}#readme"
        manifest["support"] = urls.get("Issues", f"{repo_url}/issues")
    manifest |= {
        "license": project.get("license", "MIT"),
        "keywords": project.get("keywords", []),
        "server": {
            "type": "uv",
            "entry_point": "src/seattle_open_data/mcp/server.py",
            "mcp_config": {
                "command": "uv",
                "args": LAUNCH_ARGS,
                "env": dict(ENV_FROM_CONFIG),
            },
        },
        "compatibility": {
            "platforms": ["darwin", "linux", "win32"],
            "runtimes": {"python": project["requires-python"]},
        },
        "user_config": USER_CONFIG,
        "tools": _tools(),
    }
    return manifest


def render(manifest: dict) -> str:
    """Deterministic JSON rendering used for both writing and drift comparison."""
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if manifest.json is missing or stale (do not write).",
    )
    args = parser.parse_args()

    rendered = render(build_manifest())

    if args.check:
        current = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
        if current != rendered:
            print("manifest.json is out of date; run: uv run python scripts/gen_manifest.py")
            return 1
        print("manifest.json is up to date.")
        return 0

    MANIFEST.write_text(rendered, encoding="utf-8")
    print(f"Wrote {MANIFEST.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
