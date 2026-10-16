# AGENTS.md

Canonical instructions for humans and agents working in this repo. `CLAUDE.md` points here.

## What this is

An agent-friendly MCP server for Seattle Open Data. The current design is in `docs/design.md`, probed upstream facts in `docs/upstream-facts.md`, and the decision log in `docs/decisions.md`; the contract standard is the `agent-friendly-mcp` skill, cited by rule id (e.g. `[6.repair-object]`).

## Toolchain

- uv for everything (`uv add`, `uv sync`, `uv run`); never pip.
- ruff (lint, format), ty (types), prek (hooks: `uv run prek run --all-files`).
- FastMCP 4 on the MCP Python SDK v2.

## Rules

- The tool surface is contract. `tests/snapshots/tools_list.json` pins it; regenerate with `UPDATE_SNAPSHOTS=1` only for an intended change, and note the change in `CHANGELOG.md`.
- Every failure leaves as the error envelope in `docs/design.md` (Errors). Raise `SeattleDataError` from handlers; never return ad-hoc error text.
- Every `repair` must be a literal call that validates against the named tool's input schema; `tests/test_repairs.py` enforces this for documented and emitted repairs.
- Never report an upstream fact you did not probe. Label reviewer-reported facts as such in `docs/upstream-facts.md`.
- When a design choice changes, update `docs/design.md` and add a row to the `docs/decisions.md` log.
- Live tests use the `integration` marker and run with `uv run pytest --run-integration -m integration`.

## Branching and commits

- Feature branches with a type prefix (`feat/`, `fix/`, `docs/`, ...); never commit directly to `main`.
- Conventional Commits.
- Keep a CHANGELOG entry under `## [Unreleased]` for every user-visible change. Before a version is tagged, its section is still open: a change that will ship in it goes there, not under Unreleased.
