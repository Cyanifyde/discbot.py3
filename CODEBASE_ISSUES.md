# Codebase Issues & Improvement Plan

A comprehensive review of the entire codebase, identifying incomplete implementations, bugs, missing features, security issues, and areas for improvement.

**Total Issues Found: 150+**

---

## Table of Contents
1. [Critical Issues - Modules](#critical-issues-non-functional--broken)
2. [Major Issues - Modules](#major-issues-partially-working)
3. [Minor Issues - Modules](#minor-issues-working-but-needs-improvement)
4. [Services Layer Issues](#services-layer-issues)
5. [Core Layer Issues](#core-layer-issues)
6. [Bot Client Issues](#bot-client-issues)
7. [Web/Auth Issues](#webauthentication-issues)
8. [Responders/Classes Issues](#respondersclasses-issues)
9. [Configuration Issues](#configuration-issues)
10. [Missing Features](#missing-features-by-module)
11. [Code Quality Issues](#code-quality-issues)
12. [Priority Order](#priority-order-for-fixes)
13. [Additional Audit Findings (2026-02-02)](#additional-audit-findings-2026-02-02)

---

## Critical Issues (Non-Functional / Broken)

### 1. Reports Module - Missing User Report Command
**File:** `modules/reports.py`

The reports module only has **mod-side commands** (list, view, assign, resolve, dismiss, stats). There is **no command for regular users to actually submit a report**. The `handle_report_message_context` function exists but is never registered as an actual context menu command.

**What's Missing:**
- `report @user <reason>` command for users to submit reports
- The context menu integration is defined but not wired up to Discord

**Fix Required:**
- Add a `report @user <category> <reason>` text command
- Register the context menu command in bot/client.py

---

### 2. Roles Module - Reaction Roles Not Implemented
**File:** `modules/roles.py:440-477`

The `reactionrole setup` command is a **stub** that just sends acknowledgment messages but doesn't actually:
- Collect emoji-role mappings
- Store reaction role configurations
- Handle reaction add/remove events

**Current Behavior:**
```python
# This is all it does:
await message.reply(
    "Reaction roles configured. Users can now react to get roles!\n"
    "Note: Full implementation requires event handlers."
)
```

**What's Missing:**
- Interactive emoji-role mapping collection
- Storage for reaction role configs
- `on_raw_reaction_add` / `on_raw_reaction_remove` event handlers

---

### 3. Custom Content Module - Form Submit Broken
**File:** `modules/custom_content.py:330-390`

The `form submit` command creates a Modal class but **never actually shows it** to the user. Modals require an Interaction context (slash command or button), but this is a text command.

**Current Behavior:**
```python
await message.reply(
    f"Opening form: **{form['name']}**\n"
    "Note: Full modal implementation requires interaction context."
)
# Modal is never shown
```

**What's Missing:**
- Either convert to slash command, or
- Create a button that opens the modal, or
- Implement text-based multi-step form collection

---

## Major Issues (Partially Working)

### 4. Analytics Module - No Permission/Module Checks
**File:** `modules/analytics.py:25-215`

The `handle_stats_command` function:
- Does NOT check if the analytics module is enabled
- Does NOT check user permissions
- Anyone can run stats commands

**Fix Required:**
Add these checks at the start:
```python
if not await is_module_enabled(message.guild.id, "analytics"):
    return False
if not await can_use_command(message.author, "stats"):
    # permission denied
```

---

### 5. Communication Module - Acknowledgment Button Not Integrated
**File:** `modules/communication.py:620-650`

The `AcknowledgeButton` class is defined but:
- Never attached to acknowledgment messages
- The `ack` create command doesn't send a button
- No way for users to actually acknowledge

**What's Missing:**
- Modify `_handle_ack_create` to send message with `AcknowledgeButton` view
- Register the button handler

---

### 6. Automation Module - Trigger Actions Not Executed
**File:** `modules/automation.py`

Triggers can be created and stored, but:
- No background task checks for trigger conditions
- Events like `commission_filled`, `slots_available` aren't hooked into commission module
- Actions like `notify`, `auto_close`, `auto_open` have no actual implementation

---

### 7. Art Tools Module - Rate Card Command Ambiguity
**File:** `modules/art_tools.py:96-114`

The `art help` and `art` commands conflict with `modules/art_search.py`. Both modules respond to `art` prefix.

---

## Minor Issues (Working but Needs Improvement)

### 8. Trust Module - Score Components Reference Undefined Data
**File:** `modules/trust.py:127-141`

The trust score breakdown references fields that may not exist:
- `children_count_score` - server link specific
- `upflow_status_score` - server link specific
- `link_age_score` - server link specific

These will show 0/100 for users not involved in server linking.

---

### 9. Utility Module - Export Uses Bytes Wrong
**File:** `modules/utility.py:875-877`

```python
file = discord.File(
    fp=json_data.encode(),  # This returns bytes, not a file-like object
    filename=f"user_data_{user_id}.json"
)
```

Should be:
```python
import io
file = discord.File(
    fp=io.BytesIO(json_data.encode()),
    filename=f"user_data_{user_id}.json"
)
```

---

### 10. Portfolio Module - URL Validation Too Strict
**File:** `modules/portfolio.py:28-37`

The URL pattern only accepts certain domains and requires file extension in URL. Many valid image URLs will be rejected (e.g., CDN URLs without extensions, Imgur short links).

---

### 11. Server Stats - Limited Functionality
**File:** `modules/server_stats.py`

Only has one command (`serverstats`). Could add:
- `serverstats members` - detailed member breakdown
- `serverstats activity` - recent activity metrics
- `serverstats channels` - channel statistics

---

## Missing Features by Module

### Analytics
- [ ] Module enable/disable check
- [ ] Permission checks
- [ ] Historical data visualization
- [ ] Export statistics to CSV

### Art Search
- [ ] Pagination buttons instead of `[page]` argument
- [ ] Thumbnail previews in results
- [ ] Search by date range

### Art Tools
- [ ] Separate command prefix from art_search (use `arttool` instead of `art`)

### Automation
- [ ] Background task for schedule execution
- [ ] Hook triggers into commission events
- [ ] Implement actual action execution (notify, auto_close, etc.)

### Commission Reviews
- [ ] Notification to artist when review is posted
- [ ] Average rating display in artist profile

### Commissions
- [ ] Notification system for stage changes
- [ ] Client-side view of their commissions
- [ ] Rate limiting on invoice/contract generation

### Communication
- [ ] Attach AcknowledgeButton to ack messages
- [ ] Notification when feedback status changes
- [ ] Announcement scheduling

### Custom Content
- [ ] Working form submission (button or slash command)
- [ ] Form field validation
- [ ] Conditional fields

### Invite Protection
- [ ] Invite preview (show server name before approval)
- [ ] Auto-expire pending invites

### Moderation
- [ ] Soft-ban (ban + immediate unban to clear messages)
- [ ] Lockdown command
- [ ] Slowmode control

### Portfolio
- [ ] Looser URL validation
- [ ] Direct image upload support
- [ ] Gallery view with navigation

### Reports
- [ ] User-facing `report` command
- [ ] Register context menu
- [ ] Report categories configuration

### Roles
- [ ] Full reaction roles implementation
- [ ] Button roles as alternative
- [ ] Role menu (dropdown select)

### Server Link
- [ ] Audit log for synced actions
- [ ] Rollback capability

### Server Stats
- [ ] More detailed breakdowns
- [ ] Activity graphs
- [ ] Growth metrics

### Trust
- [ ] Make score components applicable to all users (not just server-linked)
- [ ] Trust decay over time
- [ ] Trust badges/flairs

### Utility
- [ ] Fix export file creation
- [ ] Reminder system (basic reminder command)
- [ ] Poll command

### Verification
- [ ] Captcha verification option
- [ ] Age-based verification (account age check)
- [ ] Multi-step verification

---

## Code Quality Issues

### 1. Inconsistent Module Setup Pattern
Some modules call `setup_*()` explicitly, others register help at import time. Should standardize.

### 2. Mixed `message.reply` and `message.channel.send`
Inconsistent across modules. Should standardize on `message.reply(mention_author=False)`.

### 3. No Type Hints in Some Functions
Several handler functions lack return type hints.

### 4. Hardcoded Strings
Some error messages are hardcoded. Consider centralizing.

### 5. Missing Docstrings
Several helper functions lack docstrings.

---

## Priority Order for Fixes

### 🔴 CRITICAL (Fix Immediately - Security/Data Loss)

1. **Web Auth Bypass** - `web/auth.py:87-90` - Authorization skipped when guild_id missing
2. **Render Height Bug** - `services/render_service.py:156` - `min(height, 0)` always wrong
3. **Storage Race Conditions** - `core/storage.py`, `core/queueing.py` - Data corruption risk
4. **Commission Index Crash** - `services/commission_service.py:66` - Empty list access
5. **CSRF Protection** - All POST routes - No token validation

### 🟠 HIGH (Fix Soon - Broken Features)

6. **Profile Commands Unreachable** - `classes/profile.py` - 6 features completely inaccessible
7. **Reports Module** - No user report command exists
8. **Roles Module** - Reaction roles is just a stub
9. **Custom Content** - Form submit modal never shown
10. **Analytics** - No permission/module checks
11. **Automation Actions** - `_auto_close`, `_auto_open`, `_promote_waitlist` are empty
12. **Missing Event Handlers** - No reaction handlers for reaction roles
13. **Slash Commands** - Only `/commission` registered, 17+ modules text-only
14. **Delivery Bug** - `responders/delivery.py:76` - Wrong type in AllowedMentions
15. **Engine Return Bug** - `responders/engine.py:304` - Breaks trigger pipeline

### 🟡 MEDIUM (Fix When Possible - Partial Functionality)

16. **Communication** - Acknowledgment button not attached
17. **Utility Export** - Uses bytes instead of BytesIO
18. **Transaction Semantics** - All storage classes lack atomic read-modify-write
19. **Permission Validation** - Role IDs never validated
20. **Type Validation** - `from_dict()` methods accept invalid data
21. **Session Storage** - In-memory dict not production-safe
22. **Cookie Security** - Missing secure/samesite flags
23. **Rate Limiting** - No rate limits on web routes
24. **Matching Bug** - `responders/matching.py` - Contains mode broken
25. **Cooldown Memory Leak** - `responders/engine.py` - Dict never cleaned
26. **Reminder Persistence** - Lost on restart

### 🟢 LOW (Enhancements)

27. **Art Tools** - Command prefix conflict with art_search
28. **Portfolio** - URL validation too strict
29. **Server Stats** - Limited commands
30. **Trust** - Score components for non-linked users
31. **Config Names** - Mismatch between config and filenames
32. **Missing Modules** - analytics, art_search, etc. not in config
33. **Security Headers** - X-Frame-Options, CSP, etc.
34. **Template Caching** - No hot reload for templates
35. **Code Quality** - Inconsistent patterns, missing docstrings

---

## Issue Statistics

| Category | Count | Critical | High | Medium | Low |
|----------|-------|----------|------|--------|-----|
| Modules | 11 | 3 | 4 | 2 | 2 |
| Services | 20+ | 3 | 7 | 10+ | - |
| Core | 15+ | 4 | 6 | 5+ | - |
| Bot Client | 7 | 2 | 5 | - | - |
| Web/Auth | 12 | 1 | 6 | 4 | 1 |
| Responders | 10 | 1 | 4 | 3 | 2 |
| Config | 3 | - | - | 1 | 2 |
| **Total** | **80+** | **14** | **32** | **25+** | **7** |

---

## Deleted/Removed Files (from git status)
- `FEATURE_FUTUREPLAN.md` - Deleted
- `FEATURE_PLAN.md` - Deleted
- `core/federation_storage.py` - Deleted
- `modules/federation.py` - Deleted
- `services/federation_service.py` - Deleted
- `web/routes/federation.py` - Deleted

Federation feature appears to have been removed. Ensure no orphaned references remain.

---

## New Untracked Files
- `TODO.md` - Exists but not tracked
- `core/art_search_storage.py` - New
- `core/commission_review_storage.py` - New
- `core/invite_protection_storage.py` - New
- `modules/art_search.py` - New
- `modules/commission_reviews.py` - New
- `modules/invite_protection.py` - New

These should be added to git.

---

## Services Layer Issues

### CRITICAL BUGS

#### 1. Render Service - Height Clamping Bug
**File:** `services/render_service.py:156`

```python
height = max(520, min(height, 0))  # BUG: min(height, 0) always returns 0 or negative!
```

This will **always** produce incorrect height values. Should be `min(height, MAX_HEIGHT)`.

---

#### 2. Commission Service - List Index Out of Bounds
**File:** `services/commission_service.py:66`

Accesses `custom_stages[0]` without checking if list is empty - will crash on empty list.

---

#### 3. Automation Service - Empty Action Methods
**File:** `services/automation_service.py:217-235`

These methods are completely empty stubs:
- `_auto_close_commissions()` - does nothing
- `_auto_open_commissions()` - does nothing
- `_promote_waitlist()` - does nothing

The automation trigger actions **do not work**.

---

### HIGH PRIORITY ISSUES

#### 4. Analytics Service - Silent Data Corruption
**File:** `services/analytics_service.py`

- Line 38: Missing error handling in `_load_stats()` - JSON decode errors not caught
- Line 234: Bare `except (ValueError, KeyError)` silently skips errors without logging
- Line 68: `json.load()` could fail silently if file is corrupted

---

#### 5. Notification Service - Multiple Issues
**File:** `services/notification_service.py`

- Line 70: `bot.fetch_user()` could return None but assumed to work
- Line 113: Bare `except ZoneInfoNotFoundError` - other exceptions not caught
- Line 244: Orphaned exception handler logs but doesn't prevent retry loops
- Line 291: `content.get()` assumes dict, but could be string

---

#### 6. Sync Service - Guild Access Issues
**File:** `services/sync_service.py`

- Line 243: If `guild_id` is 0, continues but logs nothing
- Line 330: `action.duration` could be None but used directly in timedelta
- Line 764: `message.embeds[0]` accessed without checking if embeds list exists
- Lines 355-372: Multiple bare exception catches lose error context

---

#### 7. Trust Service - Silent Fallback
**File:** `services/trust_service.py:204`

`guild.fetch_member()` could fail but exception caught and 50.0 returned silently with no warning.

---

### MEDIUM PRIORITY

#### 8. All Services - Missing Input Validation

No services validate:
- Discord IDs (could be negative or zero)
- Enum values (invalid categories, priorities)
- String lengths (could be megabytes)
- Required dictionary keys

---

## Core Layer Issues

### CRITICAL - RACE CONDITIONS

#### 1. Storage Race Condition
**File:** `core/storage.py:169-213`

Nested lock acquisition race condition - between releasing cache_lock and shard_lock, another thread could access corrupted data.

---

#### 2. Commission Storage TOCTOU Bug
**File:** `core/commission_storage.py:121-145`

Time-Of-Check-Time-Of-Use bug in `_archive_commission()` - commission could be modified/deleted between check and archive.

---

#### 3. Queueing Race Condition
**File:** `core/queueing.py:67-75`

`enqueue()` updates in-memory state under lock but writes queue file **outside** the lock - another thread could read stale state.

---

#### 4. Moderation Storage Deadlock Risk
**File:** `core/moderation_storage.py`

`get_active_warnings()` calls `get_warnings()`, both acquire the same `_lock` - potential deadlock on concurrent calls.

---

### HIGH PRIORITY

#### 5. Missing Transaction Semantics
**File:** All `*_storage.py` files

Read-modify-write pattern is not atomic. Between `await self._read_*()` and `await self._write_*()`, another operation could modify file.

**Data loss scenario:**
```
Thread A: read portfolio -> modify entry -> [PAUSE]
Thread B: read portfolio -> modify different entry -> write
Thread A: write (overwrites Thread B's changes) <- DATA LOSS
```

---

#### 6. Permission System Gap
**File:** `core/permissions.py:133-163`

No validation that `allowed_roles` list contains valid role IDs. Invalid role IDs can be saved, causing silent permission failures.

---

#### 7. Type System Issues
**File:** `core/types.py`

- `from_dict()` constructors have no validation (user_id, guild_id could be negative)
- `LinkedMessage` IDs are `Optional[str]` but should probably be `Optional[int]`
- Inconsistent: `ScanJob.guild_id` is `str` but `Commission.artist_id` is `int`
- Line 273-274: Privacy field migration silently converts `"federation"` to `"private"` with no logging

---

#### 8. Config Migration Issues
**File:** `core/config_migration.py`

- `deep_merge()` replaces lists instead of merging - could silently lose data
- `migrate_all_guild_configs()` silently skips corrupted configs with no error logging

---

## Bot Client Issues

### CRITICAL

#### 1. Missing Slash Command Error Handler
**File:** `bot/client.py`

No `on_app_command_error` event handler - when slash commands fail, users receive no feedback.

---

#### 2. Incomplete Slash Command Registration
**File:** `bot/client.py:833-876`

Only one slash command (`/commission`) is registered. 17+ modules are loaded but their commands are TEXT only - no slash command versions.

---

#### 3. Missing Event Handlers
**File:** `bot/client.py`

Not implemented:
- `on_member_update` - No user status change tracking
- `on_user_update` - No username/avatar change logging
- `on_bulk_message_delete` - No bulk deletion logging
- `on_message_delete`/`on_message_edit` - No message history for moderation
- `on_raw_reaction_add`/`on_raw_reaction_remove` - Required for reaction roles

---

### HIGH PRIORITY

#### 4. Untracked Background Tasks
**File:** `bot/client.py`

Lines 426, 435, 265 create `asyncio.create_task()` but never track them. On shutdown, these tasks may be cancelled mid-operation causing data loss.

---

#### 5. Snapshot Lock Race Condition
**File:** `bot/client.py:581-607`

TOCTOU race between checking `snapshot_complete` and acquiring `snapshot_lock` - multiple snapshots could run concurrently.

---

#### 6. Commission Command Timeout
**File:** `bot/client.py:838-874`

The `/commission` slash command doesn't defer the interaction. If `get_commission_embed_for()` takes >3 seconds, Discord times out.

---

#### 7. Template Caching
**File:** `bot/client.py:239-240`

`default_template` is loaded once and cached forever. If an admin updates the template file, new guilds still get the old template until bot restart.

---

## Web/Authentication Issues

### CRITICAL

#### 1. Authorization Bypass
**File:** `web/auth.py:87-90`

The `@require_auth('admin')` decorator only validates admin permission if `guild_id` exists in URL. If `guild_id` is missing, **authorization is completely bypassed**.

```python
if permission_level == 'admin':
    guild_id = request.match_info.get('guild_id')
    if guild_id and not await is_guild_admin(request, user, guild_id):  # Skip if no guild_id!
        return web.Response(text='Forbidden', status=403)
```

---

### HIGH PRIORITY

#### 2. Missing Input Validation
**File:** `web/routes/owner.py:167-171`

No try-catch around `int(guild_id)` conversion - will raise unhandled exception on invalid input.

---

#### 3. No CSRF Protection on POST Requests
**File:** All POST handlers

POST requests have no per-request CSRF token validation. A malicious site could trick logged-in users into submitting forms.

---

#### 4. Insecure Session Storage
**File:** `web/auth.py:19`

Sessions stored in plain Python `dict`:
- Lost on restart
- Not thread-safe
- Not scalable
- Vulnerable to memory exhaustion

---

#### 5. Missing Security Headers
**File:** All routes

No security headers:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options`
- `Content-Security-Policy`
- `Strict-Transport-Security`

---

#### 6. Cookie Security Issues
**File:** `web/auth.py:187`

Missing `secure=True` and `samesite='Strict'` flags on session cookie.

---

#### 7. No Rate Limiting
**File:** All routes

No rate limiting on any endpoint - vulnerable to brute force and DDoS.

---

#### 8. Incomplete Admin Routes
**File:** `web/routes/admin.py`

These routes are TODO stubs returning placeholder content:
- `handle_modules`, `handle_moderation`, `handle_autoresponders`
- `handle_commands`, `handle_forms`, `handle_roles`
- `handle_automation`, `handle_commissions`, `handle_logs`, `handle_persona`

---

#### 9. Guild Admin Cache Issue
**File:** `web/auth.py:224-226`

If guild member isn't in cache, `guild.get_member()` returns None, blocking legitimate admin access.

---

## Responders/Classes Issues

### CRITICAL

#### 1. Profile Commands Unreachable
**File:** `classes/profile.py:891-1064`

6 handler methods are defined but **never called** from the main `run()` method:
- `_handle_timezone_command`
- `_handle_contact_command`
- `_handle_quiethours_command`
- `_handle_notifications_command`
- `_handle_privacy_command`
- `_handle_quickedit_command`

These features are **completely inaccessible** to users.

---

### HIGH PRIORITY

#### 2. Delivery AllowedMentions Bug
**File:** `responders/delivery.py:76-77`

Wrong parameter type - passes `role_mentions` list to `users` parameter, causing type error when user mentions are enabled.

---

#### 3. Engine Premature Return
**File:** `responders/engine.py:304`

When a handler returns None and spec.response is None, function returns False immediately instead of trying next trigger. Breaks trigger pipeline.

---

#### 4. Matching Extract Bug
**File:** `responders/matching.py:157-158`

Both branches of `if start == 0` return the same thing - input extraction doesn't work for "contains" match mode.

---

### MEDIUM PRIORITY

#### 5. Engine Memory Leak
**File:** `responders/engine.py:97-102`

Module-level `_COOLDOWNS` dict is never cleaned - keys accumulate indefinitely causing memory leak over time.

---

#### 6. Reminder Persistence
**File:** `classes/reminder.py`

Reminders stored only in memory (`_REMINDER_TASKS` set) - all reminders lost on bot restart.

---

## Configuration Issues

### Module Name Mismatches
**File:** `modules.conf` vs actual filenames

| Config Name | Actual Filename |
|-------------|-----------------|
| `serverlink` | `server_link.py` |
| `serverstats` | `server_stats.py` |
| `autoresponder` | `auto_responder.py` |

This could cause module loading failures depending on how the config is used.

---

### Missing Modules from Config

These modules exist but are NOT in `modules.conf`:
- `analytics.py`
- `art_search.py`
- `commission_reviews.py`
- `invite_protection.py`
- `dm_sender.py`
- `modules_command.py`

---

### Deleted Federation References

Federation feature was removed but check for orphaned references:
- `core/federation_storage.py` - Deleted
- `modules/federation.py` - Deleted
- `services/federation_service.py` - Deleted
- `web/routes/federation.py` - Deleted

---

## Additional Audit Findings (2026-02-02)

These are additional security/correctness/concurrency findings identified during static analysis. Some may overlap with earlier items; keep the most accurate entry as the source of truth.

## Web UI / Auth

### CRITICAL

#### 1. Stored XSS in Admin Panel (Guild Name Injection)
**File:** `web/routes/admin.py:75`

Guild names are interpolated into HTML via f-strings without HTML escaping (`<h3>{g["name"]}</h3>`).

**Why it matters:** Persistent XSS against authenticated admins (session hijack, CSRF, arbitrary UI actions).

**Fix Required:** Render via an auto-escaping template engine or `html.escape()` for all interpolated values; add a strict CSP.

---

#### 2. Stored XSS in Owner Panel (Guild Name Injection + JS String Context)
**File:** `web/routes/owner.py:112`, `web/routes/owner.py:116`

`guild.name` is inserted into HTML and also into an inline JS `confirm('...')` string without proper escaping for either context.

**Why it matters:** Persistent XSS against the bot owner session.

**Fix Required:** Template engine with escaping; remove inline JS; escape separately for HTML and JS string contexts.

---

### HIGH PRIORITY

#### 3. Unbounded In-Memory Session Store (Memory DoS)
**File:** `web/auth.py:19`

OAuth “state” and user sessions are stored in a global dict with no TTL eviction, size cap, or cleanup for abandoned logins.

**Why it matters:** Repeated `/auth/login` calls can grow memory unbounded and crash the process.

**Fix Required:** Add TTL + periodic cleanup + max session count; use Redis/DB-backed sessions with expiry.

---

#### 4. `secure=True` Cookie Always Enabled (Breaks HTTP Local Deployments)
**File:** `web/auth.py:189`

Session cookie is always set with `secure=True`, which prevents cookies from being set/sent over HTTP (default redirect URI is `http://localhost:8080/...`).

**Why it matters:** Auth “randomly” fails in common local setups; encourages insecure operator workarounds.

**Fix Required:** Make `secure` conditional on HTTPS (config/env); document production requirements.

---

#### 5. OAuth Access Token Stored in Session Data
**File:** `web/auth.py:181`

Discord OAuth `access_token` is stored in-process without encryption or rotation.

**Why it matters:** Memory disclosure/log dumps become account compromise; long-lived replay window.

**Fix Required:** Don’t store tokens unless required; if required, encrypt + store with short TTL.

---

#### 6. Admin Authorization Depends on Guild Member Cache
**File:** `web/auth.py:226`

Uses `guild.get_member(...)` only; cache misses deny legitimate admins.

**Why it matters:** Fragile authorization; leads to pressure to weaken checks.

**Fix Required:** Fall back to `await guild.fetch_member(...)` on cache miss; handle rate limits.

---

### MEDIUM PRIORITY

#### 7. OAuth “State” and Sessions Share the Same Keyspace
**File:** `web/auth.py:111`, `web/auth.py:136`

The same dict stores both pending OAuth states and active sessions; callback checks only “key exists” rather than “entry is a pending state with TTL”.

**Why it matters:** Increases likelihood of state/session confusion bugs and weakens CSRF guarantees.

**Fix Required:** Separate pending-state storage from session storage; enforce TTL and validate entry shape.

---

#### 8. No Server-Side Session Expiry Enforcement
**File:** `web/auth.py:57`

Cookie has `max_age`, but server-side sessions have no enforced expiration unless logout happens.

**Why it matters:** Stolen tokens can remain valid indefinitely.

**Fix Required:** Store `expires_at` per session and reject/evict expired sessions in middleware.

---

#### 9. Logout Is a GET Endpoint (CSRF-able Nuisance Logout)
**File:** `web/auth.py:50`

Logout is routed as GET.

**Why it matters:** Cross-site requests can trigger logout, causing nuisance and potentially interfering with workflows.

**Fix Required:** Make logout POST with CSRF token (or double-submit cookie pattern).

---

## Rendering

### CRITICAL

#### 10. SSRF / Possible Local File Read via WeasyPrint Resource Fetching
**File:** `services/render_service.py:259`, `templates/renders/rate_card_minimal.html:133`

WeasyPrint will fetch resources referenced in HTML/CSS. Templates include `<img src="{{ profile.image }}">` and per-rate `<img src="{{ rate_data.image }}">` and those values can be user-controlled (stored data URIs or URLs).

**Why it matters:** SSRF to internal networks/metadata endpoints; potential `file://` reads depending on fetcher behavior; bandwidth/CPU exhaustion by large remote resources.

**Fix Required:** Use a hardened WeasyPrint `url_fetcher` (block `file://`, block private IPs, allowlist schemes/hosts, enforce size/time). Prefer supporting only `data:` URIs for embedded images.

---

### HIGH PRIORITY

#### 11. Rendering Runs Synchronously in the Async Event Loop
**File:** `services/render_service.py:260`

`write_pdf()` and PDF rasterization are CPU-heavy and run in the event loop thread.

**Why it matters:** Under load, rendering blocks moderation/event handling and causes Discord timeouts.

**Fix Required:** Offload rendering to `asyncio.to_thread()` or a worker process with concurrency limits.

---

#### 12. Import-Time Hard Failure When Optional Deps Are Missing
**File:** `services/render_service.py:411`

`render_service = get_render_service()` instantiates at import time and raises if Jinja2/Pillow are missing.

**Why it matters:** A missing optional dependency bricks the whole bot even if rendering features aren’t used.

**Fix Required:** Lazy-initialize inside command handlers; avoid import-time instantiation; provide a graceful fallback.

---

## Core / Storage / Queueing

### CRITICAL

#### 13. Lost Updates from Concurrent Guild Config Writes (No Locking)
**File:** `core/config_migration.py:199`

`update_guild_module_data()` performs read-modify-write without per-guild locking; concurrent updates overwrite each other.

**Why it matters:** Security-critical state (permissions, scanner state, verification state) can silently revert.

**Fix Required:** Add a per-guild `asyncio.Lock` around read/modify/write; merge updates rather than overwrite.

---

#### 14. Deterministic Temp Filename in Atomic Writes (Cross-Write Clobber)
**File:** `core/io_utils.py:29`

`write_json_atomic()` uses a fixed `.tmp` name, so concurrent writes clobber the temp file.

**Why it matters:** Non-deterministic final content and corrupted JSON.

**Fix Required:** Use a unique temp filename (PID + random suffix) and then `os.replace`.

---

#### 15. Queue Acknowledgement Is Not Concurrency-Safe
**File:** `core/queueing.py:205`

Multiple worker tasks call `_ack_processed()` concurrently and mutate shared `pending_order/pending_done/read_offset_bytes/queued_jobs` with no lock.

**Why it matters:** Queue state corruption, stuck offsets, dropped jobs, incorrect compaction/state.

**Fix Required:** Serialize ACK processing with an `asyncio.Lock` or a single ACK consumer task.

---

### HIGH PRIORITY

#### 16. QueueStore Updates State Before Writing Queue Line
**File:** `core/queueing.py:67`

`QueueStore.enqueue()` increments `queued_jobs` then appends to `queue.jsonl`. If append fails, state overcounts.

**Why it matters:** Scanner can misreport and stall due to inconsistent state.

**Fix Required:** Write the queue line first, then update state; add recovery on partial failure.

---

#### 17. Jobs Are ACKed Even When Processing Fails (Silent Job Loss)
**File:** `core/queueing.py:202`

Worker loop ACKs jobs regardless of exceptions/timeouts.

**Why it matters:** Transient Discord/network failures cause permanent scan loss (enforcement reliability gap).

**Fix Required:** Add retry with backoff; only ACK on success or after explicit terminal failure; add dead-letter queue.

---

#### 18. `magic_bytes_valid` Is Over-Permissive
**File:** `core/utils.py:121`

Uses `sig in haystack` which allows false positives (e.g., `BM` anywhere in first 512 bytes).

**Why it matters:** Type gating can be bypassed; downstream assumes “safe image”.

**Fix Required:** Require correct offsets (`startswith` for most formats); implement a real RIFF/WEBP structure check.

---

#### 19. Scanner Allows Voice/Stage Channels in Message Fetch Path
**File:** `core/queueing.py:247`, `core/queueing.py:338`

Channel type check includes `VoiceChannel`/`StageChannel` in a code path that calls `fetch_message()`.

**Why it matters:** Unexpected exceptions, wasted work, dropped jobs.

**Fix Required:** Restrict to message-capable channel types only.

---

## Responders

### CRITICAL

#### 20. Regex ReDoS via Auto-Responder `regex` Match Mode
**File:** `responders/matching.py:69`

Potentially attacker-controlled regex patterns can run against attacker-controlled message text with Python’s backtracking regex engine and no timeout.

**Why it matters:** CPU DoS from a single message (catastrophic backtracking).

**Fix Required:** Disallow arbitrary regex triggers or move to a safe regex engine (RE2); enforce pattern complexity/length limits.

---

### HIGH PRIORITY

#### 21. Delivery Success Flag Overwritten per Target
**File:** `responders/delivery.py:213-217`

`handled = await _send_*()` overwrites prior success. A later failure makes the function report False even if it sent earlier.

**Why it matters:** Upstream logic can mis-handle “sent” vs “not sent”.

**Fix Required:** Use `handled = handled or await _send_*()` per target.

---

#### 22. `delay_seconds` / Cooldown / Limits Parsing Can Crash on Bad Config
**File:** `responders/delivery.py:205`, `responders/engine.py:91`, `responders/matching.py:194-202`

Direct `float(...)` / `int(...)` casts can raise `ValueError` on malformed JSON types.

**Why it matters:** A single bad config entry breaks responders and can spam logs.

**Fix Required:** Validate/normalize config schema on load; safe-parse with fallbacks.

---

### MEDIUM PRIORITY

#### 23. Dynamic Handler Import Is a Risky Plugin Boundary
**File:** `responders/engine.py:151`

Config selects handler paths that are dynamically imported from `classes.*`.

**Why it matters:** A compromised config becomes “choose what code runs” within the codebase surface.

**Fix Required:** Restrict to an allowlist of handlers; validate handler types; consider signed configs.

---

#### 24. Delivery Exceptions Are Swallowed Without Logging
**File:** `responders/delivery.py` (exception `continue` in send loop)

Broad exception handling drops failures silently.

**Why it matters:** Operationally impossible to diagnose responder failures.

**Fix Required:** Add rate-limited structured logging and metrics on send failures.

---

#### 25. Auto-Responder Engine Uses Globals Instead of the `AutoResponderEngine` Class
**File:** `responders/engine.py:41`

There is an `AutoResponderEngine` class with per-instance caches, but runtime paths use module-level `_HANDLER_CACHE`/`_COOLDOWNS`.

**Why it matters:** Confusing state model; high refactor risk.

**Fix Required:** Pick one approach (instance-based or module-global) and delete the other.

---

## Bot / Modules / Misc

### HIGH PRIORITY

#### 26. Unbounded Background Task Spawning Per Message
**File:** `bot/client.py:426`, `bot/client.py:435`

Per-message `asyncio.create_task()` without backpressure or concurrency caps.

**Why it matters:** Task explosion under load → memory pressure and latency spikes.

**Fix Required:** Use bounded queues/workers; coalesce activity updates; rate-limit responder calls.

---

#### 27. Portfolio Rate Image Processing: No Size/Pixel Limits
**File:** `modules/portfolio.py:864`, `modules/portfolio.py:872`

Downloads full attachment bytes and feeds into PIL without strict size/pixel caps.

**Why it matters:** Decompression bombs and memory exhaustion.

**Fix Required:** Enforce max download size, timeouts, and `Image.MAX_IMAGE_PIXELS`; catch `DecompressionBombError`.

---

#### 28. Module Enable/Disable Registry Is Incomplete
**File:** `core/permissions.py:21`

Many runtime modules are not listed in `AVAILABLE_MODULES`, but still call `is_module_enabled`; unknown modules default to enabled and are not controllable via `modules` command.

**Why it matters:** Operators cannot reliably disable risky/broken modules.

**Fix Required:** Ensure every `MODULE_NAME` is registered in `AVAILABLE_MODULES` and seeded into per-guild permissions.

---

#### 29. Date/Duration Helpers Can Crash or Behave Incorrectly
**File:** `core/utils.py:191`, `core/utils.py:209`, `core/utils.py:355`

- `apply_decay()` math appears inconsistent with its comment (likely off by 100x).
- `parse_deadline()` can return naive datetimes from ISO strings.
- `parse_duration_extended()` accepts unbounded integers and can overflow `timedelta`.

**Why it matters:** User inputs can crash scheduling/expiry features; trust decay becomes incorrect.

**Fix Required:** Normalize to timezone-aware UTC datetimes; clamp duration components; catch overflow; fix decay math or comment.

---

### MEDIUM PRIORITY

#### 30. `resolve_repo_path` Returns Absolute Paths Unchanged
**File:** `core/paths.py:14`

Absolute paths are accepted and returned.

**Why it matters:** Increases risk of future path traversal / arbitrary file read bugs if user input ever flows here.

**Fix Required:** Provide a strict “repo-relative only” resolver and reject absolute paths by default.

---

#### 31. `is_safe_relative_path` Does Not Prevent Symlink Escapes
**File:** `core/utils.py:72`

Rejects `..` but doesn’t resolve symlinks; a “safe” relative path can still escape via a symlinked directory.

**Why it matters:** File IO helpers can be tricked into reading/writing outside the repo boundary.

**Fix Required:** Resolve and enforce that the final path stays within `BASE_DIR`.

---

#### 32. Duplicate Implementations of `magic_bytes_valid`
**File:** `services/hash_checker.py:58`, `core/utils.py:121`

Two implementations will drift.

**Why it matters:** Fixes in one path won’t harden the other.

**Fix Required:** Centralize and reuse one vetted implementation.

---

#### 33. No Automated Test Suite
**File:** (no `tests/` directory present)

No tests covering auth, queue correctness, parsing, or storage concurrency.

**Why it matters:** Security-critical regressions will ship unnoticed.

**Fix Required:** Add unit tests for config updates, queue ack ordering, URL fetch restrictions, parsing normalization, and XSS escaping.
