# seattle-open-data

[![CI](https://github.com/briandconnelly/seattle-open-data/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/briandconnelly/seattle-open-data/actions/workflows/ci.yml)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fbriandconnelly%2Fseattle-open-data%2Fmain%2Fpyproject.toml)](pyproject.toml)
[![License: MIT](https://img.shields.io/github/license/briandconnelly/seattle-open-data)](LICENSE)

An agent-friendly [MCP](https://modelcontextprotocol.io/) server for [Seattle Open Data](https://data.seattle.gov/) (Socrata SODA2).

**Status: alpha (0.1.0).** All four tools work against the live portal, with curated guides for seven flagship datasets. See [evals/results](evals/results) for the agent evaluation.

## Tools

| Tool | Purpose |
| --- | --- |
| `seattle_data_search_datasets` | Find datasets by topic |
| `seattle_data_describe_dataset` | Columns, row count, time coverage, place fields, geo columns |
| `seattle_data_query_dataset` | Bounded reads and aggregates (SoQL clauses or raw SoQL) |
| `seattle_data_get_reference` | Capability summary, SoQL help, guides, error catalog |

## Run

```bash
uv run seattle-open-data          # stdio
```

Claude Code:

```bash
claude mcp add seattle-open-data -- uv run --directory /path/to/seattle-open-data seattle-open-data
```

Optional: set `SOCRATA_APP_TOKEN` to use a Socrata application token. A rejected token is reported as `app_token_rejected`, never silently dropped.

To build the installable MCPB bundle (needs the `mcpb` CLI): `make mcpb`.

## Develop

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check && uv run ty check
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py   # after an intended tool-surface change
uv run pytest --run-integration -m integration            # live tests against data.seattle.gov
uv run python evals/run.py [--host claude|codex] <out_dir> [task_id ...]
uv run python evals/report.py <out_dir> --answers
```

## Release

Tag `vX.Y.Z` after bumping the version: the release workflow builds and smoke-tests the wheel, sdist, and `.mcpb`, publishes to PyPI after maintainer approval, and attaches all three to a GitHub Release. See [docs/releasing.md](docs/releasing.md) for the steps, the TestPyPI rehearsal, and one-time setup.

## License

MIT
