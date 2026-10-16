# Host captures

Milestone 1 exit test (d): what real hosts send to the server and receive from it.
Each capture is a JSONL log written by `scripts/capture_server.py` (one record per request: method, params, and the `tools/list` result).

## 2026-10-15

Both hosts were run headless against server 0.1.0 with the same task: call `seattle_data_get_reference(topic="soql", item_id="dates")`, trigger an `invalid_argument` on `seattle_data_query_dataset`, and quote the description and parameters they received for that tool.

| | Claude Code 2.1.301 | Codex CLI 0.162.1 |
| --- | --- | --- |
| Protocol | 2026-07-28 (`server/discover`, stateless) | 2025-06-18 (`initialize` handshake) |
| Requests seen | discover, prompts/list, resources/list, tools/list, tools/call | initialize, tools/list, tools/call ×2 |
| Tool loading | Deferred: the model fetched definitions through ToolSearch before calling | Preloaded |
| Description received | Full text, untruncated | Full text, **prefixed with the whole server `instructions` block**, followed by a generated TypeScript declaration |
| Parameters received | All 13 for query, including `near` sub-fields | All 13, including `near` sub-fields |
| Arguments at server | Exactly as sent (`{"topic": "soql", "item_id": "dates"}`), no stringified containers | Same |
| Error envelope | Read `code`, `details.field`, `temporary`, `retry_after_ms` correctly | Same |

Findings that constrain the design:
- Codex repeats `instructions` in every tool description, so its length is paid once per tool (4× today). Keep `instructions` short; the 24 KiB `tools/list` budget does not see this cost because it is added host-side.
- Codex negotiates 2025-06-18, older than the plan's 2025-11-25 compatibility floor, and FastMCP 4.1 served it. `tests/test_legacy_protocol.py` covers the handshake path, but FastMCP's legacy client negotiates 2025-11-25, so 2025-06-18 itself is covered only by this capture.
- No host stringified object or array arguments, so the [3.stringified-shim] is not needed.
- Prefix truncation of `instructions` was not observed on either host; no prefix length has been measured yet.

Known gap: the Claude Code log lacks the record for its second (failing) call. The capture wrapper at that time dropped records for calls that raised internally; the transcript confirms the envelope reached the model. The wrapper was fixed before the Codex run, whose log has both calls.
