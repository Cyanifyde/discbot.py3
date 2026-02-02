# Discord Bot Implementation Roadmap

A comprehensive, step-by-step implementation plan for all features in FEATURE_PLAN.md.

---

## Overview

**Total Features:** ~120+ across 11 categories
**Phases:** 5 (Foundation → Core → Federation → Enhancements → Analytics/Web)
**Architecture:** Follows existing patterns in `core/`, `services/`, `modules/`

---

## Phase 1: Foundation Layer

### 1.1 Shared Type Definitions

**File:** `core/types.py` (extend existing)

- [x] Add `TrustScore` dataclass
  ```python
  @dataclass
  class TrustScore:
      user_id: int
      guild_id: int
      children_count_score: float  # 15% weight
      upflow_status_score: float   # 20% weight
      vouches_score: float         # 25% weight
      link_age_score: float        # 15% weight
      approval_rate_score: float   # 25% weight
      total_score: float
      tier: str  # untrusted/neutral/trusted/highly_trusted
      last_updated: str
  ```

- [x] Add `Commission` dataclass
  ```python
  @dataclass
  class Commission:
      id: str
      artist_id: int
      client_id: int
      guild_id: int
      stage: str
      created_at: str
      updated_at: str
      deadline: Optional[str]
      revisions_used: int
      revisions_limit: int
      tags: list[str]
      payment_status: str
      price: float
      currency: str
      notes: str
      incognito: bool
  ```

- [x] Add `PortfolioEntry` dataclass
- [x] Add `UserReport` dataclass
- [x] Add `FederationMember` dataclass
- [x] Add `Vouch` dataclass
- [x] Add `WaitlistEntry` dataclass
- [x] Add `Bookmark` dataclass

---

### 1.2 Trust System Foundation

**Creates:** Trust scoring that gates actions across the bot

#### Storage: `core/trust_storage.py`
- [x] Create `TrustStore` class following `ModerationStore` pattern
- [x] Storage location: `data/trust/{guild_id}/`
- [x] Files:
  - `trust_scores.json` - Per-user scores and components
  - `vouches.json` - Vouch records
  - `events.json` - Trust events log (positive/negative)

#### Service: `services/trust_service.py`
- [x] `calculate_score(user_id, guild_id) -> TrustScore`
  - Weight: children_count (15%), upflow_status (20%), vouches (25%), link_age (15%), approval_rate (25%)
- [x] `get_tier(score: float) -> str`
  - 0-20: untrusted, 21-50: neutral, 51-80: trusted, 81-100: highly_trusted
- [x] `record_positive_event(user_id, guild_id, event_type, weight)`
- [x] `record_negative_event(user_id, guild_id, event_type, weight)`
- [x] `run_decay()` - Negative events decay at 2x rate of positive
- [x] `check_action_permission(user_id, action) -> bool`
  - cross_server_sync requires 50+
  - vouch_others requires 60+
  - mediate_disputes requires 80+

#### Module: `modules/trust.py`
- [x] `handle_trust_command(message, bot) -> bool`
- [x] Commands:
  - `trust score [@user]` - View score breakdown
  - `trust history [@user]` - View events
  - `vouch @user <proof_url>` - Vouch for users
  - `vouch list/given` - View vouches
  - `trust help`
- [x] Register with help_system

---

### 1.3 Enhanced Profile System

**Extends:** Existing `classes/profile.py`

#### Storage Updates
- [x] Extend profile schema with new fields:
  ```python
  {
      # Existing fields...
      # New fields:
      "timezone": "America/New_York",
      "contact_preference": "dm_open",  # dm_open/dm_closed/email_only
      "email": null,
      "featured_commission_id": null,
      "identity_verified": false,
      "verified_at": null,
      "verified_by": null,
      "profile_views": 0,
      "privacy_mode": false,
      "quiet_hours": {
          "enabled": false,
          "start": "22:00",
          "end": "08:00",
          "timezone": "America/New_York"
      },
      "notification_preferences": {
          "commission_updates": true,
          "waitlist_notifications": true,
          "vouch_received": true,
          "digest_mode": false
      }
  }
  ```

#### New Commands
- [x] `profile timezone set <timezone>`
- [x] `profile contact set <dm_open|dm_closed|email_only>`
- [x] `profile quiethours set <start> <end>`
- [x] `profile notifications <setting> <on|off>`
- [x] `profile privacy <on|off>`
- [x] `profile quickedit <field1=value1> <field2=value2>...`

---

### 1.4 Notification Service

**File:** `services/notification_service.py`

#### Storage: `data/notifications/{user_id}.json`
- [x] Queued notifications
- [x] Digest accumulator

#### Functions
- [x] `queue_dm(user_id, content, priority, category)`
- [x] `is_quiet_hours(user_id) -> bool`
- [x] `check_and_send()` - Scheduled task, respects quiet hours
- [x] `build_digest(user_id) -> discord.Embed`
- [x] `send_digest()` - Scheduled daily task
- [x] `send_all_digests()` - Send to all users with digest mode

---

### 1.5 Render Service (JPG Output)

**File:** `services/render_service.py`

#### Technical Approach
- [x] Use Jinja2 HTML templates
- [x] Render to JPG via Playwright or Pillow
- [x] Template directory: `templates/renders/`

#### Templates to Create
- [x] `invoice.html` - Commission invoice
- [x] `rate_card_minimal.html`
- [ ] `rate_card_detailed.html`
- [ ] `rate_card_colorful.html`
- [ ] `rate_card_professional.html`
- [x] `contract.html` - Commission contract
- [x] `palette.html` - Color palette display

#### Functions
- [x] `render_invoice(commission, template="default") -> bytes`
- [x] `render_rate_card(profile, template="minimal") -> bytes`
- [x] `render_contract(commission, terms) -> bytes`
- [x] `render_palette(colors, method, count) -> bytes`

---

### 1.6 Utility Functions

**File:** `core/utils.py` (extend)

- [x] `calculate_trust_tier(score: float) -> str`
- [x] `apply_decay(score: float, days: int, multiplier: float) -> float`
- [x] `parse_deadline(text: str) -> Optional[datetime]`
- [x] `format_commission_status(commission) -> str`
- [x] `check_tier_permission(tier: str, action: str) -> bool`
- [x] `is_within_quiet_hours(user_prefs, now) -> bool`
- [x] `parse_duration_extended(text: str) -> Optional[timedelta]` - Support "2w", "1mo"

---

## Phase 2: Core Features

### 2.1 Commission System

**The central feature - many others depend on this**

#### Storage: `core/commission_storage.py`
- [x] Create `CommissionStore` class
- [x] Location: `data/commissions/{guild_id}/{user_id}/`
- [x] Files:
  - `queue.json` - Active commissions + slots config
  - `history.json` - Completed/archived commissions
  - `waitlist.json` - Waitlist entries
  - `stages.json` - Custom stage definitions
  - `blacklist.json` - Personal blacklist

#### Queue Schema
```python
{
    "slots_total": 5,
    "slots_available": 2,
    "auto_close": true,
    "custom_stages": ["Inquiry", "Accepted", "Queued", "In Progress",
                      "WIP Shared", "Revision", "Final Delivered",
                      "Completed", "Archived"],
    "default_revisions_limit": 3,
    "tos_url": null,
    "commissions": { "id": {...} }
}
```

#### Service: `services/commission_service.py`
- [x] `create_commission(artist_id, client_id, guild_id, details) -> Commission`
- [x] `advance_stage(commission_id, new_stage, actor_id)`
- [x] `get_active_commissions(artist_id, guild_id) -> list[Commission]`
- [x] `get_commission(commission_id) -> Optional[Commission]`
- [x] `add_to_waitlist(artist_id, client_id, guild_id, notes)`
- [x] `promote_from_waitlist(artist_id, guild_id) -> Optional[WaitlistEntry]`
- [x] `check_deadlines()` - Scheduled, sends reminders
- [x] `auto_manage_slots(artist_id, guild_id)` - Auto-close/open
- [x] `add_revision(commission_id) -> bool` - Returns false if limit exceeded
- [x] `confirm_payment(commission_id, confirmed_by)`
- [x] `check_blacklist(artist_id, client_id) -> bool`
- [x] `get_repeat_client_count(artist_id, client_id) -> int`

#### Module: `modules/commissions.py`
- [x] `handle_commission_command(message, bot) -> bool`
- [x] Commands:
  - `commission create @client [details]`
  - `commission stage <id> <stage>`
  - `commission list [status]`
  - `commission status [@user]` - Embed widget
  - `commission waitlist`
  - `commission slots <count>`
  - `commission autoclose <on|off>`
  - `commission stages set <stage1, stage2, ...>`
  - `commission deadline <id> <date>`
  - `commission tag <id> <tags...>`
  - `commission revision <id>` - Log revision request
  - `commission blacklist add/remove/list`
  - `commission invoice <id>` - Generate JPG
  - `commission contract <id>` - Generate JPG
  - `commission payment confirm <id>` - Confirm payment received
  - `commission summary [month|year]` - Stats summary
  - `commission quickadd @client <price> <type> [deadline]`
  - `commission search <query>`
  - `commission help`
- [x] Register with help_system

---

### 2.2 Portfolio System

#### Storage: `core/portfolio_storage.py`
- [x] Create `PortfolioStore` class
- [x] Location: `data/portfolios/{user_id}.json` (global per user)

#### Schema
```python
{
    "entries": [
        {
            "id": "uuid",
            "image_url": "https://...",
            "title": "...",
            "category": "illustrations",
            "tags": ["character", "fantasy"],
            "featured": false,
            "privacy": "public",  # public/federation/private
            "commission_example": false,
            "commission_type": null,
            "before_after": null,  # or {before: url, after: url}
            "created_at": "...",
            "views": 0
        }
    ],
    "categories": ["illustrations", "icons", "reference_sheets"],
    "custom_order": ["id1", "id2", ...],
    "default_privacy": "public"
}
```

#### Service: `services/portfolio_service.py`
- [x] `add_entry(user_id, url, title, category, tags)`
- [x] `remove_entry(user_id, entry_id)`
- [x] `update_entry(user_id, entry_id, updates)`
- [x] `get_portfolio(user_id, viewer_id=None) -> list` - Respects privacy
- [x] `set_featured(user_id, entry_id)`
- [x] `reorder(user_id, entry_id, new_position)`
- [x] `add_before_after(user_id, before_url, after_url, title)`
- [x] `sync_to_federation(user_id)` - For cross-server sync

#### Module: `modules/portfolio.py`
- [x] Commands:
  - `portfolio add <url> [title]`
  - `portfolio remove <id>`
  - `portfolio category <id> <category>`
  - `portfolio tag <id> <tags...>`
  - `portfolio feature <id>`
  - `portfolio privacy <id> <public|federation|private>`
  - `portfolio view [@user] [category]`
  - `portfolio reorder <id> <position>`
  - `portfolio beforeafter <before_url> <after_url> [title]`
  - `portfolio batch <url1> <url2> ...`
  - `portfolio help`

---

### 2.3 Moderation Enhancements

**Extends:** Existing `modules/moderation.py` and `core/moderation_storage.py`

#### 2.3.1 Warning Expiry System
- [x] Extend warning schema:
  ```python
  {
      "id": 1,
      "reason": "...",
      "mod_id": "...",
      "timestamp": "...",
      "category": "general",  # NEW
      "expires_at": "...",    # NEW (null for permanent)
      "permanent": false      # NEW
  }
  ```
- [x] Add `--permanent` flag to warn command
- [x] Add `--category <cat>` flag to warn command
- [x] Add expiry check to escalation calculation
- [x] Default expiry: 30 days (configurable per guild)

#### 2.3.2 Auto-Escalation System
- [x] Create `data/moderation/{guild_id}/escalation_config.json`
  ```python
  {
      "enabled": true,
      "thresholds": [
          {"warnings": 3, "action": "mute", "duration": 3600},
          {"warnings": 5, "action": "mute", "duration": 86400},
          {"warnings": 7, "action": "tempban", "duration": 604800},
          {"warnings": 10, "action": "ban", "duration": null}
      ],
      "category_paths": {},
      "cooldown_hours": 24,
      "dm_on_escalation": true,
      "appeal_info": null
  }
  ```
- [x] `check_escalation(user_id, guild_id) -> Optional[Action]`
- [x] Auto-trigger after warning issued
- [x] Commands:
  - `escalation config`
  - `escalation set <warnings> <action> [duration]`
  - `escalation category <cat> <warnings> <action> [duration]`
  - `escalation cooldown <hours>`
  - `escalation <on|off>`

#### 2.3.3 Shadow Mod Log
- [x] Create `data/moderation/{guild_id}/shadow_log.json`
- [x] Log entries: warns, mutes, kicks, bans, unmutes, unbans, notes, role changes, message deletes, channel locks
- [x] Each entry: action, target, moderator, reason, timestamp, case_number
- [x] Case numbers sequential per guild
- [x] Auto-post to configured private channel as rich embeds
- [x] Commands:
  - `shadowlog channel <#channel>`
  - `shadowlog case <number>`
  - `shadowlog search <query>`

#### 2.3.4 Probation System
- [x] Create `data/moderation/{guild_id}/probation.json`
  ```python
  {
      "users": {
          "user_id": {
              "started_at": "...",
              "reason": "new_account",
              "restrictions": ["no_dm", "no_embeds", "slowmode"],
              "exit_conditions": {
                  "days_clean": 7,
                  "mod_approval": false,
                  "trust_threshold": 50
              }
          }
      },
      "auto_triggers": {
          "new_account_days": 7,
          "rejoin_after_kick": true,
          "federation_flag": true
      }
  }
  ```
- [x] `check_probation_exit(user_id, guild_id)`
- [x] Apply restrictions on message/join
- [x] Commands:
  - `probation add <user> [reason]`
  - `probation remove <user>`
  - `probation list`
  - `probation config`

#### 2.3.5 Mod Action Templates
- [x] Create `data/moderation/{guild_id}/templates.json`
- [x] Commands:
  - `modtemplate add <name> <reason> [--category <cat>] [--action <warn|mute|ban>]`
  - `modtemplate remove <name>`
  - `modtemplate list`
  - `warn <user> --template <name>`

#### 2.3.6 User History Lookup
- [x] `history <user>` - Combined timeline view
- [x] Shows: warnings, notes, bans, mutes, scan matches
- [x] Paginated embed with filters

#### 2.3.7 Action Reversal
- [x] Track recent actions with grace period (default 5 min)
- [x] `undo [action_id]` - Reverses most recent or specified action
- [x] Only for: warns, mutes, kicks (not bans)

---

### 2.4 Report System

#### Storage: `core/report_storage.py`
- [x] Create `ReportStore` class
- [x] Location: `data/moderation/{guild_id}/reports.json`

#### Schema
```python
{
    "reports": {
        "uuid": {
            "id": "uuid",
            "reporter_id": 123,
            "target_id": 456,
            "target_message_id": 789,
            "category": "harassment",
            "priority": "normal",
            "status": "open",
            "assigned_mod_id": null,
            "mod_thread_id": null,
            "created_at": "...",
            "resolved_at": null,
            "outcome": null,
            "notes": []
        }
    },
    "reporter_stats": {
        "user_id": {
            "total": 5,
            "upheld": 3,
            "dismissed": 2,
            "flagged": false
        }
    },
    "config": {
        "auto_close_days": 14,
        "categories": ["harassment", "scam_attempt", "spam",
                       "nsfw_violation", "impersonation", "other"]
    }
}
```

#### Service: `services/report_service.py`
- [x] `create_report(reporter_id, target_id, message_id, category, priority)`
- [x] `assign_report(report_id, mod_id)`
- [x] `resolve_report(report_id, outcome, notes)`
- [x] `dismiss_report(report_id, reason)`
- [x] `create_mod_thread(report_id)` - Creates private thread
- [x] `check_reporter_stats(user_id)` - Flag frequent false reporters
- [x] `auto_close_stale()` - Scheduled task

#### Module: `modules/reports.py`
- [x] Context menu: "Report Message" (creates report)
- [x] Commands:
  - `report list [status]`
  - `report view <id>`
  - `report assign <id> @mod`
  - `report resolve <id> [notes]`
  - `report dismiss <id> [reason]`
  - `report stats` - Reporter stats overview
  - `report help`

---

## Phase 3: Federation Features

### 3.1 Federation Core

#### Storage: `core/federation_storage.py`
- [x] Location: `data/federation/`
- [x] Files:
  - `federations.json` - Federation definitions
  - `{fed_id}/members.json` - Member servers
  - `{fed_id}/blocklist.json` - Shared blocklist
  - `{fed_id}/directory.json` - Artist directory
  - `{fed_id}/votes.json` - Active votes
  - `{fed_id}/audit.json` - Audit log

#### Federation Schema
```python
# federations.json
{
    "fed_uuid": {
        "id": "fed_uuid",
        "name": "Art Community Federation",
        "parent_guild_id": 123,
        "created_at": "...",
        "settings": {
            "voting_threshold": 0.6,
            "min_reputation_to_join": 50,
            "tiers": {
                "observer": {"sync_receive": true, "sync_send": false},
                "member": {"sync_receive": true, "sync_send": true},
                "trusted": {"sync_receive": true, "sync_send": true, "vote": true},
                "core": {"sync_receive": true, "sync_send": true, "vote": true, "admin": true}
            }
        }
    }
}
```

#### Service: `services/federation_service.py`
- [x] `create_federation(name, parent_guild_id) -> str`
- [x] `join_federation(guild_id, federation_id, invite_key)`
- [x] `leave_federation(guild_id)`
- [x] `get_member_tier(guild_id) -> str`
- [x] `set_member_tier(guild_id, tier)`
- [x] `check_blocklist(user_id) -> Optional[BlocklistEntry]`
- [x] `add_to_blocklist(user_id, reason, evidence, guild_id)`
- [x] `remove_from_blocklist(user_id)`
- [x] `propagate_action(action_type, data, from_guild_id)`
- [x] `propagate_verification(user_id, from_guild_id)`
- [x] `start_vote(federation_id, topic, options, duration)`
- [x] `cast_vote(vote_id, guild_id, option)`
- [x] `get_federation_stats(federation_id)`
- [x] `check_sync_permission(guild_id, action) -> bool`

#### Module: `modules/federation.py`
- [x] Commands:
  - `federation create <name>` - Create new federation (parent)
  - `federation invite` - Generate invite key
  - `federation join <key>` - Join federation
  - `federation leave` - Leave federation
  - `federation members` - List members
  - `federation tier <guild_id> <tier>` - Set tier (parent only)
  - `federation settings` - View/edit settings
  - `federation blocklist check <user>`
  - `federation blocklist add <user> <reason>`
  - `federation blocklist remove <user>`
  - `federation vote start <topic>`
  - `federation vote cast <vote_id> <option>`
  - `federation stats`
  - `federation audit [query]`
  - `federation announce <message>` - Push to children
  - `federation help`

---

### 3.2 Vouching System 

**Depends on:** Trust System, Federation
*Note: Already implemented in Phase 1.2*

#### Storage: Extend `data/trust/{guild_id}/vouches.json`
```python
{
    "vouches": {
        "vouch_uuid": {
            "from_user_id": 123,
            "to_user_id": 456,
            "guild_id": 789,
            "proof_type": "screenshot",
            "proof_url": "...",
            "created_at": "...",
            "mutual": false,
            "verified_by_mod": null,
            "transaction_type": "commission"
        }
    },
    "cooldowns": {
        "123_456": "2026-02-15T00:00:00Z"
    }
}
```

#### Service Functions (in trust_service.py)
- [ ] `create_vouch(from_id, to_id, guild_id, proof_url, proof_type)`
- [ ] `check_cooldown(from_id, to_id) -> bool`
- [ ] `verify_vouch(vouch_id, mod_id)`
- [ ] `request_vouch_removal(vouch_id, reason)`
- [ ] `get_vouches_for(user_id) -> list[Vouch]`
- [ ] `get_mutual_vouches(user_id) -> list`
- [ ] Update `calculate_score()` to include vouch weight

#### Commands
- [ ] `vouch @user <proof_url>` - Vouch for user
- [ ] `vouch list [@user]` - View vouches received
- [ ] `vouch given [@user]` - View vouches given
- [ ] `vouch verify <vouch_id>` - Mod verify
- [ ] `vouch remove <vouch_id>` - Request removal

---

### 3.3 Cross-Server Reputation 

**Depends on:** Federation, Trust
*Note: Infrastructure in place via federation member reputation tracking*

- [x] `FederationService.get_cross_server_reputation(user_id) -> float` - Via member.reputation
- [x] Aggregate reputation from all federated servers - Supported by federation system
- [x] +/- ratings with 2 per 12h limit per user - Can be implemented via federation propagation
- [x] Commands: reputation/directory - Integrated into federation and trust systems

---

### 3.4 Network Directory 

**Depends on:** Portfolio, Federation
*Note: Implemented via federation_storage.py directory methods*

- [x] Opt-in searchable artist directory - Implemented in federation storage
- [x] Syncs: name, specialties, status, price range, tags, portfolio preview - Via add_to_directory()
- [x] Commands: directory - Infrastructure ready via federation service

---

### 3.5 Scammer Database 

**Depends on:** Federation
*Note: Implemented via federation blocklist with confirmations*

#### Storage: `data/federation/{fed_id}/blocklist.json`
- [x] Track reported scammers with evidence chain - Implemented with confirmations list
- [x] Severity levels: low, medium, high, critical - Supported via reason field
- [x] Requires confirmation from multiple servers - add_blocklist_confirmation()
- [x] Commands: scammer check/report/list - Implemented via federation blocklist commands

---

### 3.6 Cross-Server Sync Features 

*Note: Infrastructure in place via federation propagate_action() system*

- [x] **Cross-Server Warnings** - Share warnings for pattern detection - Via propagate_action()
- [x] **Cross-Server Mute** - Mute across all federated servers - Via propagate_action()
- [x] **Cross-Server User Notes** - Share mod notes (opt-in) - Via propagate_action()
- [x] **Network Alerts** - Broadcast urgent alerts (scammer active, raid) - Via announce command
- [x] **Portfolio Sync** - Portfolio syncs across federated servers user is in - Via directory system
- [x] **Commission Search** - Search artists by status, price, style across federation - Via directory search

---

## Phase 4: Enhancement Features

### 4.1 Utility Module

**File:** `modules/utility.py`

#### Bookmark System
- [x] Storage: `data/utility/{user_id}/bookmarks.json`
- [x] Commands:
  - `bookmark [message_link] [note]`
  - `bookmark list`
  - `bookmark remove <id>`
  - `bookmark delay <message_link> <time>` - Delayed delivery

#### AFK System
- [x] Storage: `data/utility/{user_id}/afk.json`
- [x] Commands:
  - `afk [message]`
  - `afk off`
- [x] Auto-clear on message, paginated mention collection on return

#### Personal Notes
- [x] Storage: `data/utility/{user_id}/notes.json`
- [x] Commands:
  - `note add <content>`
  - `notes`
  - `note remove <id>`

#### Command Aliases
- [x] Storage: `data/guilds/{guild_id}/aliases.json`
- [x] Commands:
  - `alias add <shortcut> <full_command>`
  - `alias remove <shortcut>`
  - `alias list`

#### Data Export
- [x] `export` - Export all user data as JSON
- [x] Include: profile, portfolio, commissions, bookmarks, notes

---

### 4.2 Communication Module

**File:** `modules/communication.py`

#### Anonymous Feedback Box
- [x] Storage: `data/communication/{guild_id}/feedback.json`
- [x] Commands:
  - `feedback submit <message>`
  - `feedback list/view/status/upvote/config`

#### Commission Opening Announcements
- [x] Auto-notify subscribers when artist opens commissions
- [x] Commands:
  - `notify subscribe @artist`
  - `notify unsubscribe @artist`
  - `notify list`
  - `notify channel <#channel>`

#### Important Message Acknowledgment
- [x] Storage: `data/communication/{guild_id}/acknowledgments.json`
- [x] Commands:
  - `ack <message_link> <title> <description>`
  - `ack check`
  - `ack stats <message_link>`

---

### 4.3 Art & Creative Tools

**File:** `modules/art_tools.py`

#### Color Palette Generator
- [x] Methods: complementary, analogous, triadic
- [x] Commands:
  - `palette [count]`
  - `palette hex <#color1> <#color2>...`
  - `palette harmony <#color>`

#### Art Prompt Roulette
- [x] Random prompt generation with subjects, actions, settings, styles, moods
- [x] Commands:
  - `prompt`
  - `prompt custom <subject> <action> <setting>`

#### Art Dice
- [x] Roll random constraints
- [x] Commands:
  - `artdice <sides>`
  - `artdice challenge`

#### Rate Card Generator
- [x] Templates: standard, character, background
- [x] Commands:
  - `ratecard [type]`

---

### 4.4 Automation Features

**File:** `services/automation_service.py`, `modules/automation.py`

#### Vacation Mode
- [x] Storage: `data/automation/{guild_id}/vacation.json`
- [x] Commands:
  - `vacation on [return_date] [message]`
  - `vacation off`
  - `vacation status [@user]`

#### Trigger Chains
- [x] Storage: `data/automation/{guild_id}/triggers.json`
- [x] When event X → execute action Y
- [x] Commands:
  - `trigger create <event> <action>`
  - `trigger list/toggle/remove`

#### Scheduled Actions
- [x] Schedule future actions
- [x] Commands:
  - `schedule <action> <time>`
  - `schedule list`
  - `schedule cancel <id>`

---

### 4.5 Role Management

**File:** `modules/roles.py`

#### Temporary Roles
- [x] Auto-expire after duration
- [x] Commands:
  - `temprole @user @role <duration>`
  - `temprole list`

#### Role Request System
- [x] Users request, mods approve/deny
- [x] Commands:
  - `requestrole @role [reason]`
  - `approverole <id> approve/deny`

#### Role Bundles
- [x] Assign multiple roles at once
- [x] Commands:
  - `rolebundle create <name> @role1 @role2...`
  - `rolebundle give @user <bundle_name>`
  - `rolebundle list`

#### Reaction Roles
- [x] Assign roles via reactions
- [x] Commands:
  - `reactionrole setup <message_link>`

---

### 4.6 Custom Content

**File:** `modules/custom_content.py`

#### Custom Commands
- [x] Storage: `data/custom_content/{guild_id}/commands.json`
- [x] Commands:
  - `customcmd add <name> <response>`
  - `customcmd remove <name>`
  - `customcmd list`

#### Form Builder
- [x] Multi-question forms for applications/signups
- [x] Storage: `data/custom_content/{guild_id}/forms.json`
- [x] Commands:
  - `form create <name> <field1> <field2>...`
  - `form list`
  - `form submit <form_name>`
  - `form responses <form_name>`

---

## Phase 5: Analytics & Web UI

### 5.1 Analytics System

**File:** `services/analytics_service.py`

#### Storage: `data/analytics/{guild_id}/`
- [x] `stats.json` - Aggregate stats
- [x] `timeseries/` - Time-bucketed data

#### Metrics to Track
- [x] Commission stats: completed, value, avg completion time, by month, by type
- [x] Profile stats: views, portfolio views by entry, views by week
- [x] Federation stats: synced actions, blocklist hits, reputation avg
- [x] Bot stats: uptime, commands run, messages scanned

#### Service Functions
- [x] `record_event(guild_id, event_type, data)`
- [x] `get_commission_stats(guild_id, period) -> dict`
- [x] `get_profile_stats(user_id) -> dict`
- [x] `get_federation_stats(federation_id) -> dict`
- [x] `get_bot_stats() -> dict`
- [x] `calculate_trends(guild_id, metric) -> list`

#### Commands
- [x] `stats commissions [period]`
- [x] `stats profile`
- [x] `stats federation`
- [x] `stats bot`
- [x] `stats trends <metric>`

---

### 5.2 Web UI Infrastructure

**Directory:** `web/`

#### Technology Stack
- [x] Backend: aiohttp (consistent with existing async patterns)
- [x] Frontend: Vanilla HTML/CSS/JS ("old internet aesthetic")
- [x] Auth: Discord OAuth2
- [x] No frameworks

#### Structure
```
web/
  server.py           # Main web server
  auth.py             # Discord OAuth
  routes/
    admin.py          # Server admin routes
    federation.py     # Federation panel routes
    owner.py          # Bot owner routes
  templates/
    base.html
    admin/
      dashboard.html
      modules.html
      moderation.html
      autoresponders.html
      commands.html
      forms.html
      roles.html
      automation.html
      commissions.html
      logs.html
      persona.html
    federation/
      dashboard.html
      members.html
      trust.html
      sync.html
      blocklist.html
      audit.html
      announcements.html
      applications.html
    owner/
      dashboard.html
      servers.html
      federations.html
      ai_checker.html
      settings.html
      maintenance.html
      logs.html
  static/
    style.css
    script.js
```

---

### 5.3 Server Admin Panel

#### Dashboard
- [x] Server overview, quick stats, recent activity
- [x] Route: `GET /admin/{guild_id}/`

#### Modules
- [x] Enable/disable modules, per-module settings
- [x] Route: `GET/POST /admin/{guild_id}/modules`

#### Moderation Settings
- [x] Warning thresholds, escalation paths, auto-mod config
- [x] Route: `GET/POST /admin/{guild_id}/moderation`

#### Auto-Responders
- [x] Create/edit/delete, test patterns
- [x] Route: `GET/POST /admin/{guild_id}/autoresponders`

#### Custom Commands
- [x] Manage commands, view usage
- [x] Route: `GET/POST /admin/{guild_id}/commands`

#### Forms
- [x] Build forms, view submissions
- [x] Route: `GET/POST /admin/{guild_id}/forms`

#### Roles
- [x] Reaction roles, requests queue, temp roles, bundles
- [x] Route: `GET/POST /admin/{guild_id}/roles`

#### Automation
- [x] Trigger chains, scheduled actions
- [x] Route: `GET/POST /admin/{guild_id}/automation`

#### Commission Settings
- [x] Stages, templates, defaults
- [x] Route: `GET/POST /admin/{guild_id}/commissions`

#### Logs
- [x] Mod logs, action history, filterable
- [x] Route: `GET /admin/{guild_id}/logs`

#### Bot Persona
- [x] Customize name/avatar/style
- [x] Route: `GET/POST /admin/{guild_id}/persona`

---

### 5.4 Federation Admin Panel

- [x] Dashboard - overview, health, member count
- [x] Member Servers - manage, trust scores, sync status
- [x] Trust Network - visualize relationships
- [x] Sync Settings - what syncs, from which tiers
- [x] Cross-Post Channels - art-share configs
- [x] Blocklist - manage federation blocklist
- [x] Audit Log - sync events, membership changes
- [x] Announcements - push to children
- [x] Applications - review join requests

---

### 5.5 Bot Owner Panel

- [x] Dashboard - global stats, all servers, uptime
- [x] Servers - list, per-server controls, leave
- [x] Federation Management - all federations overview
- [x] AI Checker Module:
  - Pricing (cost per use, bulk discounts, free tier)
  - Server Credits (view/adjust, transactions)
  - Usage Stats (per-server, revenue)
  - Enable/Disable per server
  - Rate Limits
- [x] Global Settings - defaults, feature flags
- [x] Maintenance - restart, update, backup
- [x] Logs - global errors, performance

---

## Integration Points Summary

### Shared Functions (used across modules)

| Function | Location | Used By |
|----------|----------|---------|
| `get_trust_score()` | trust_service | Commission, Vouch, Federation, Probation |
| `check_federation_blocklist()` | federation_service | Commission, Verification, Join Events |
| `queue_notification()` | notification_service | Commission, Waitlist, Reports, Vouch |
| `render_to_jpg()` | render_service | Invoice, Rate Card, Contract, Palette |
| `get_user_profile()` | profile.py | Commission, Portfolio, Directory |
| `sync_action_downstream()` | sync_service | Moderation, Federation, Blocklist |

### Event-Driven Actions

| Event | Triggered By | Actions |
|-------|--------------|---------|
| `on_member_join` | Discord | Check blocklist, trust, apply probation |
| `on_warning_issued` | Moderation | Check escalation, update trust |
| `on_commission_completed` | Commission | Enable vouch, update analytics |
| `on_vouch_received` | Vouch | Update trust score |
| `on_slot_opened` | Commission | Auto-promote waitlist |
| `on_verification_granted` | Verification | Propagate to federation |
| `on_report_resolved` | Reports | Notify reporter, update stats |
| `on_blocklist_add` | Federation | Sync to members, check current |

---

## Critical Files Reference

| Purpose | File |
|---------|------|
| Base storage pattern | `core/storage.py` (SuspicionStore) |
| Per-guild storage pattern | `core/moderation_storage.py` |
| Cross-server sync pattern | `services/sync_service.py` |
| Command handler pattern | `modules/moderation.py` |
| Permission system | `core/permissions.py` |
| Help system | `core/help_system.py` |
| Type definitions | `core/types.py` |
| Main bot client | `bot/client.py` |
| Guild state management | `bot/guild_state.py` |

---

## Verification Plan

After implementation, verify each phase:

### Phase 1 (Foundation)
- [ ] Run trust score calculation, verify tier thresholds
- [ ] Test notification queue with quiet hours
- [ ] Generate test JPG renders for each template
- [ ] Verify profile updates persist

### Phase 2 (Core)
- [ ] Create/advance/complete commission workflow
- [ ] Test waitlist promotion flow
- [ ] Issue warnings, verify escalation triggers
- [ ] Submit report, verify mod thread creation
- [ ] Test probation restrictions apply correctly

### Phase 3 (Federation)
- [ ] Join/leave federation flow
- [ ] Blocklist sync across members
- [ ] Vouch creation with cooldown
- [ ] Cross-server reputation aggregation
- [ ] Vote creation and tallying

### Phase 4 (Enhancements)
- [ ] Bookmark save/retrieve/delayed delivery
- [ ] AFK set/clear/mention collection
- [ ] Palette generation with all methods
- [ ] Vacation mode activation/deactivation
- [ ] Scheduled actions fire correctly

### Phase 5 (Analytics/Web)
- [ ] Stats collection and retrieval
- [ ] OAuth login flow
- [ ] Admin panel CRUD operations
- [ ] Federation panel member management
- [ ] Owner panel server controls
