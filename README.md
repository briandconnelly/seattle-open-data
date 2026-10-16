# seattle-open-data

An agent-friendly [MCP](https://modelcontextprotocol.io/) server for [Seattle Open Data](https://data.seattle.gov/) (Socrata SODA2).

**Status: pre-release (milestone 1, contract spike).** The tool and error contracts are frozen in tests; `seattle_data_get_reference` works, while search, describe, and query return `not_implemented` until milestones 2 and 3. See [PLAN.md](PLAN.md).

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

Optional: set `SOCRATA_APP_TOKEN` to use a Socrata application token.

## Develop

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check && uv run ty check
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contract.py   # after an intended tool-surface change
```

## License

MIT
