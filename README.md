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

## Install

The server runs over stdio. The `uvx` commands below need [uv](https://docs.astral.sh/uv/).

**Claude Desktop:** download `seattle-open-data-<version>.mcpb` from the [latest release](https://github.com/briandconnelly/seattle-open-data/releases/latest) and open it with Claude Desktop.

**Claude Code:**

```bash
claude mcp add seattle-open-data -- uvx seattle-open-data
```

**Codex:**

```bash
codex mcp add seattle-open-data -- uvx seattle-open-data
```

**Any other MCP client:** run `uvx seattle-open-data` as a stdio server:

```json
{
  "mcpServers": {
    "seattle-open-data": { "command": "uvx", "args": ["seattle-open-data"] }
  }
}
```

**Optional app token:** set `SOCRATA_APP_TOKEN` to use a [Socrata application token](https://dev.socrata.com/docs/app-tokens.html), for example `claude mcp add seattle-open-data -e SOCRATA_APP_TOKEN=... -- uvx seattle-open-data`. The bundle asks for it in Claude Desktop's settings. A rejected token is reported as `app_token_rejected`, never silently dropped.

## Develop

From a checkout:

```bash
uv sync
uv run seattle-open-data                                  # run the server over stdio
uv run pytest
uv run ruff check && uv run ruff format --check && uv run ty check
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py   # after an intended tool-surface change
uv run pytest --run-integration -m integration            # live tests against data.seattle.gov
uv run python evals/run.py [--host claude|codex] <out_dir> [task_id ...]
uv run python evals/report.py <out_dir> --answers
```

To build the MCPB bundle locally (needs the `mcpb` CLI): `make mcpb`.

## Release

Tag `vX.Y.Z` after bumping the version: the release workflow builds and smoke-tests the wheel, sdist, and `.mcpb`, publishes to PyPI after maintainer approval, and attaches all three to a GitHub Release. See [docs/releasing.md](docs/releasing.md) for the steps, the TestPyPI rehearsal, and one-time setup.

## License

MIT
