# Architecture

R3 Markdown Export has one engine and two presentation adapters.

The Python package owns configuration, source indexing, Markdown link
resolution, export construction, atomic writes and the local HTTP interface.
The command-line adapter uses R3CLI for human output while keeping readiness
JSON machine-readable.

The Obsidian plugin contains no exporter implementation. It starts the
installed `markdown-export` executable, reads one protocol-versioned readiness
message, verifies `/api/health` and embeds the loopback interface in a sandboxed
iframe. All views share one process, which stops when its final consumer closes.

Protocol version 1 uses these readiness fields: `event`, `url`,
`protocol_version` and `root`. The health response contains `status`,
`protocol_version` and `root`. Consumers must reject non-loopback URLs,
unexpected roots and incompatible protocol versions.
