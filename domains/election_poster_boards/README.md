# Election Poster Boards domain

This directory owns the election poster-board application. It is intentionally
separate from the municipal-document domains (`minutes`, `reiki`, search,
OpenAPI and MCP).

## Boundary

- `php/`: LINE session handling, authorization and the mutable SQLite stores.
- `tools/`: database initialization and poster-board source-data maintenance.
- `schema/`: SQLite schemas owned by this domain.
- `tests/`: boundary and compatibility tests.

The public `/boards/*` and `/line/*` paths remain in `app/` as stable HTTP
entry points. Shared code is limited to municipality identity/canonical slugs,
configuration loading and common site assets. This domain must not depend on
the minutes/reiki crawlers, OpenSearch, `/api/search`, or `/api/document`.

Runtime data remains below `data/boards/`. Existing installations may still
have the shared LINE user database at `data/users.sqlite`; the PHP runtime
reads that legacy location until it is explicitly migrated to
`data/boards/users.sqlite`.

Deployment excludes `boards.sqlite`, `tasks.sqlite`, and `users.sqlite` from
the directory mirror so remote location edits, progress, and identities are
never overwritten by a code deployment.
