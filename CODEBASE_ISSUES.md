# Engineering Backlog: Reliability / Performance / Maintainability

_Note: Add/remove items as needed to track known issues. Don’t put TODOs in `.py` files; track them here._

## Remaining Issues

Each item keeps a stable `ID` so you can reference it in PRs/issues without renumbering.

- **[ID 3] Critical — Per-message disk I/O for AFK auto-clear will melt the bot under real traffic**
  - Type: Performance
  - Location (as of 2026-02-02): `bot/client.py` → `DiscBot.on_message` → lines 291–299
  - Recommendation: Keep AFK state in memory; periodically flush to disk

- **[ID 4] Critical — Unbounded `asyncio.create_task()` per message (no backpressure, no tracking)**
  - Type: Reliability
  - Location (as of 2026-02-02): `bot/client.py` → `DiscBot.on_message` → lines 436 and 445
  - Recommendation: Replace per-message `create_task` with a bounded `asyncio.Queue` + worker(s)

- **[ID 10] High — Permission/module checks hit disk repeatedly**
  - Type: Performance
  - Location (as of 2026-02-02): `core/config_migration.py` → `get_guild_module_data` → line 257
  - Recommendation: Introduce a per-guild in-memory cache with TTL + invalidate-on-write

- **[ID 13] High — Bookmark delivery loop scans the filesystem every cycle**
  - Type: Performance
  - Location (as of 2026-02-02): `modules/utility.py` → `deliver_pending_bookmarks` → lines 528 and 538
  - Recommendation: Maintain a single "pending deliveries" index (or persist due timestamps in one file/db)

- **[ID 21-44] Medium/low severity refactoring and maintainability issues**

## Removed Components

- **Web UI (admin/owner panel)**
  - Status (as of 2026-02-02): Removed — `web/` package, routes, and static assets deleted.
