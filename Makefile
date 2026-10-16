.PHONY: manifest mcpb test lint

manifest:
	uv run python scripts/gen_manifest.py

MCPB_VERSION := $(shell python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')

# Requires the mcpb CLI: npm install -g @anthropic-ai/mcpb
mcpb: manifest
	uv run python scripts/build_bundle.py

test:
	uv run pytest

lint:
	uv run ruff check
	uv run ruff format --check
	uv run ty check
