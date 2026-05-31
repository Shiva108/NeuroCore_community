# Quickstart CLI Example

This example exercises the smallest useful NeuroCore flow in the public repo:
capture a note, query it back, and inspect the result.

## Capture

```bash
neurocore capture --request-json '{"bucket":"research","content":"community repo note","content_format":"markdown","source_type":"note"}'
```

## Query

```bash
neurocore query --request-json '{"query_text":"community repo","namespace":"default","allowed_buckets":["research"],"sensitivity_ceiling":"standard"}'
```

## Expected Result

The query should return the captured record through the same shared core
interfaces used by the CLI, HTTP adapter, and MCP adapter.
