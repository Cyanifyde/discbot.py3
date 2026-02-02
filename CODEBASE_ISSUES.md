# Engineering Backlog: Reliability / Performance / Maintainability

## Remaining Issues

3) [SEVERITY: Critical] – Per-message disk I/O for AFK auto-clear will melt the bot under real traffic
Type: Performance
Location: `bot/client.py` → `DiscBot.on_message` → line 284
Recommendation: Keep AFK state in memory with periodic flush

4) [SEVERITY: Critical] – Unbounded `asyncio.create_task()` per message (no backpressure, no tracking)
Type: Reliability
Location: `bot/client.py` → `DiscBot.on_message` → lines 426, 435
Recommendation: Replace per-message `create_task` with a bounded `asyncio.Queue` + worker(s)

10) [SEVERITY: High] – Permission/module checks hit disk repeatedly
Type: Performance
Location: `core/config_migration.py` → `get_guild_module_data` → line 240
Recommendation: Introduce a per-guild in-memory cache with TTL + invalidate-on-write

13) [SEVERITY: High] – Bookmark delivery loop scans entire filesystem every minute
Type: Performance
Location: `modules/utility.py` → `deliver_pending_bookmarks` → lines 528, 536
Recommendation: Maintain a single "pending deliveries" index file

14) [SEVERITY: High] – Web auth session store is a global dict with no eviction
Type: Reliability
Location: `web/auth.py` → module global `sessions` → line 19
Recommendation: Split pending-state store from sessions; add eviction

21-44) Various medium/low severity refactoring and maintainability issues
