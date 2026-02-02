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
