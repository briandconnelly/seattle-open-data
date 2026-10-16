"""The MCPB manifest is generated from pyproject and the live server; it must not drift."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_manifest", ROOT / "scripts" / "gen_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_matches_generator():
    gen = _generator()
    assert (ROOT / "manifest.json").read_text() == gen.render(gen.build_manifest())


def test_token_is_sensitive_and_optional():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    token = manifest["user_config"]["app_token"]
    assert token["sensitive"] is True and token["required"] is False
    assert manifest["server"]["mcp_config"]["env"] == {
        "SOCRATA_APP_TOKEN": "${user_config.app_token}"
    }


def test_manifest_icons_are_square_pngs_of_declared_size():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    sources = {icon["src"] for icon in manifest["icons"]}
    assert manifest["icon"] in sources
    for icon in manifest["icons"]:
        data = (ROOT / icon["src"]).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", icon["src"]
        width, height = int.from_bytes(data[16:20]), int.from_bytes(data[20:24])
        assert f"{width}x{height}" == icon["size"], icon["src"]
