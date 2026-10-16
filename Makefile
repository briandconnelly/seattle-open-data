.PHONY: manifest mcpb test lint

manifest:
	uv run python scripts/gen_manifest.py

MCPB_VERSION := $(shell python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')

# Requires the mcpb CLI: npm install -g @anthropic-ai/mcpb
mcpb: manifest
	mcpb validate manifest.json
	mkdir -p dist
	mcpb pack . dist/seattle-open-data-$(MCPB_VERSION).mcpb

test:
	uv run pytest

lint:
	uv run ruff check
	uv run ruff format --check
	uv run ty check
