# Parse APIs — start here

This package holds typed clients for this project's Parse APIs, generated
by the `parse` CLI (installed editable — imports resolve from any cwd).
`parse sync` overwrites generated files; change an API at parse.bot, then
re-sync.

Fresh checkout? The generated tree (clients, per-API READMEs/examples,
`src/parse_apis/CLAUDE.md`) does not exist until `uv run parse sync`. Headless
callers should export `PARSE_API_KEY` first; `uv run parse login` is the
interactive alternative. Until sync, importing a generated client raises
ModuleNotFoundError. Guided setup: `uv run parse help`;
diagnose a broken install: `uv run parse doctor`.

Workflow (run from the project root):
- Check auth / base URL / host-trust:   uv run parse whoami --json
- See which APIs your key can call:      uv run parse list --json
- Generate / refresh typed clients:      uv run parse sync --json
  (machine-readable status: written / skipped / quarantined / failures)

Import a client:  from parse_apis.<slug> import <Root>
The exact <slug> and <Root> come from the generated tree, not `parse list`
(sync may sanitize/pin slugs): read `src/parse_apis/<slug>/README.md`, or
`uv run parse sync --json` (its `written[]` lists each slug + root).

Before list/search calls, pass `limit=` unless exhaustive traversal is
explicitly requested — each page is a live, billed request.

Conventions + the API index (generated; appear after `parse sync`):
`src/parse_apis/CLAUDE.md`. Per-API (generated): `src/parse_apis/<slug>/README.md`
+ runnable `example.py`.
