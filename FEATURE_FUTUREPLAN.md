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
- [ ] Create `TrustStore` class following `ModerationStore` pattern
- [ ] Storage location: `data/trust/{guild_id}/`
- [ ] Files:
  - `trust_scores.json` - Per-user scores and components
  - `vouches.json` - Vouch records
  - `events.json` - Trust events log (positive/negative)

#### Service: `services/trust_service.py`
- [ ] `calculate_score(user_id, guild_id) -> TrustScore`
  - Weight: children_count (15%), upflow_status (20%), vouches (25%), link_age (15%), approval_rate (25%)
- [ ] `get_tier(score: float) -> str`
  - 0-20: untrusted, 21-50: neutral, 51-80: trusted, 81-100: highly_trusted
- [ ] `record_positive_event(user_id, guild_id, event_type, weight)`
- [ ] `record_negative_event(user_id, guild_id, event_type, weight)`
- [ ] `run_decay()` - Negative events decay at 2x rate of positive
- [ ] `check_action_permission(user_id, action) -> bool`
  - cross_server_sync requires 50+
  - vouch_others requires 60+
  - mediate_disputes requires 80+

#### Module: `modules/trust.py`
- [ ] `handle_trust_command(message, bot) -> bool`
- [ ] Commands:
  - `trust score [@user]` - View score breakdown
  - `trust history [@user]` - View events
  - `trust help`
- [ ] Register with help_system

---

### 1.3 Enhanced Profile System

**Extends:** Existing `classes/profile.py`

#### Storage Updates
- [ ] Extend profile schema with new fields:
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
- [ ] `profile timezone set <timezone>`
- [ ] `profile contact set <dm_open|dm_closed|email_only>`
- [ ] `profile quiethours set <start> <end>`
- [ ] `profile notifications <setting> <on|off>`
- [ ] `profile privacy <on|off>`
- [ ] `profile quickedit <field1=value1> <field2=value2>...`

---

### 1.4 Notification Service

**File:** `services/notification_service.py`

#### Storage: `data/notifications/{user_id}.json`
- [ ] Queued notifications
- [ ] Digest accumulator

#### Functions
- [ ] `queue_dm(user_id, content, priority, category)`
- [ ] `is_quiet_hours(user_id) -> bool`
- [ ] `check_and_send()` - Scheduled task, respects quiet hours
- [ ] `build_digest(user_id) -> discord.Embed`
- [ ] `send_digest()` - Scheduled daily task

---

### 1.5 Render Service (JPG Output)

**File:** `services/render_service.py`

#### Technical Approach
- [ ] Use Jinja2 HTML templates
- [ ] Render to JPG via Playwright or Pillow
- [ ] Template directory: `templates/renders/`

#### Templates to Create
- [ ] `invoice.html` - Commission invoice
- [ ] `rate_card_minimal.html`
- [ ] `rate_card_detailed.html`
- [ ] `rate_card_colorful.html`
- [ ] `rate_card_professional.html`
- [ ] `contract.html` - Commission contract
- [ ] `palette.html` - Color palette display

#### Functions
- [ ] `render_invoice(commission, template="default") -> bytes`
- [ ] `render_rate_card(profile, template="minimal") -> bytes`
- [ ] `render_contract(commission, terms) -> bytes`
- [ ] `render_palette(colors, method, count) -> bytes`

---

### 1.6 Utility Functions

**File:** `core/utils.py` (extend)

- [ ] `calculate_trust_tier(score: float) -> str`
- [ ] `apply_decay(score: float, days: int, multiplier: float) -> float`
- [ ] `parse_deadline(text: str) -> Optional[datetime]`
- [ ] `format_commission_status(commission) -> str`
- [ ] `check_tier_permission(tier: str, action: str) -> bool`
- [ ] `is_within_quiet_hours(user_prefs, now) -> bool`
- [ ] `parse_duration_extended(text: str) -> Optional[timedelta]` - Support "2w", "1mo"

---

## Phase 2: Core Features

### 2.1 Commission System

**The central feature - many others depend on this**

#### Storage: `core/commission_storage.py`
- [ ] Create `CommissionStore` class
- [ ] Location: `data/commissions/{guild_id}/{user_id}/`
- [ ] Files:
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
- [ ] `create_commission(artist_id, client_id, guild_id, details) -> Commission`
- [ ] `advance_stage(commission_id, new_stage, actor_id)`
- [ ] `get_active_commissions(artist_id, guild_id) -> list[Commission]`
- [ ] `get_commission(commission_id) -> Optional[Commission]`
- [ ] `add_to_waitlist(artist_id, client_id, guild_id, notes)`
- [ ] `promote_from_waitlist(artist_id, guild_id) -> Optional[WaitlistEntry]`
- [ ] `check_deadlines()` - Scheduled, sends reminders
- [ ] `auto_manage_slots(artist_id, guild_id)` - Auto-close/open
- [ ] `add_revision(commission_id) -> bool` - Returns false if limit exceeded
- [ ] `confirm_payment(commission_id, confirmed_by)`
- [ ] `check_blacklist(artist_id, client_id) -> bool`
- [ ] `get_repeat_client_count(artist_id, client_id) -> int`

#### Module: `modules/commissions.py`
- [ ] `handle_commission_command(message, bot) -> bool`
- [ ] Commands:
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
- [ ] Register with help_system

---

### 2.2 Portfolio System

#### Storage: `core/portfolio_storage.py`
- [ ] Create `PortfolioStore` class
- [ ] Location: `data/portfolios/{user_id}.json` (global per user)

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
- [ ] `add_entry(user_id, url, title, category, tags)`
- [ ] `remove_entry(user_id, entry_id)`
- [ ] `update_entry(user_id, entry_id, updates)`
- [ ] `get_portfolio(user_id, viewer_id=None) -> list` - Respects privacy
- [ ] `set_featured(user_id, entry_id)`
- [ ] `reorder(user_id, entry_id, new_position)`
- [ ] `add_before_after(user_id, before_url, after_url, title)`
- [ ] `sync_to_federation(user_id)` - For cross-server sync

#### Module: `modules/portfolio.py`
- [ ] Commands:
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
- [ ] Extend warning schema:
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
- [ ] Add `--permanent` flag to warn command
- [ ] Add `--category <cat>` flag to warn command
- [ ] Add expiry check to escalation calculation
- [ ] Default expiry: 30 days (configurable per guild)

#### 2.3.2 Auto-Escalation System
- [ ] Create `data/moderation/{guild_id}/escalation_config.json`
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
- [ ] `check_escalation(user_id, guild_id) -> Optional[Action]`
- [ ] Auto-trigger after warning issued
- [ ] Commands:
  - `escalation config`
  - `escalation set <warnings> <action> [duration]`
  - `escalation category <cat> <warnings> <action> [duration]`
  - `escalation cooldown <hours>`
  - `escalation <on|off>`

#### 2.3.3 Shadow Mod Log
- [ ] Create `data/moderation/{guild_id}/shadow_log.json`
- [ ] Log entries: warns, mutes, kicks, bans, unmutes, unbans, notes, role changes, message deletes, channel locks
- [ ] Each entry: action, target, moderator, reason, timestamp, case_number
- [ ] Case numbers sequential per guild
- [ ] Auto-post to configured private channel as rich embeds
- [ ] Commands:
  - `shadowlog channel <#channel>`
  - `shadowlog case <number>`
  - `shadowlog search <query>`

#### 2.3.4 Probation System
- [ ] Create `data/moderation/{guild_id}/probation.json`
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
- [ ] `check_probation_exit(user_id, guild_id)`
- [ ] Apply restrictions on message/join
- [ ] Commands:
  - `probation add <user> [reason]`
  - `probation remove <user>`
  - `probation list`
  - `probation config`

#### 2.3.5 Mod Action Templates
- [ ] Create `data/moderation/{guild_id}/templates.json`
- [ ] Commands:
  - `modtemplate add <name> <reason> [--category <cat>] [--action <warn|mute|ban>]`
  - `modtemplate remove <name>`
  - `modtemplate list`
  - `warn <user> --template <name>`

#### 2.3.6 User History Lookup
- [ ] `history <user>` - Combined timeline view
- [ ] Shows: warnings, notes, bans, mutes, scan matches
- [ ] Paginated embed with filters

#### 2.3.7 Action Reversal
- [ ] Track recent actions with grace period (default 5 min)
- [ ] `undo [action_id]` - Reverses most recent or specified action
- [ ] Only for: warns, mutes, kicks (not bans)

---

### 2.4 Report System

#### Storage: `core/report_storage.py`
- [ ] Create `ReportStore` class
- [ ] Location: `data/moderation/{guild_id}/reports.json`

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
- [ ] `create_report(reporter_id, target_id, message_id, category, priority)`
- [ ] `assign_report(report_id, mod_id)`
- [ ] `resolve_report(report_id, outcome, notes)`
- [ ] `dismiss_report(report_id, reason)`
- [ ] `create_mod_thread(report_id)` - Creates private thread
- [ ] `check_reporter_stats(user_id)` - Flag frequent false reporters
- [ ] `auto_close_stale()` - Scheduled task

#### Module: `modules/reports.py`
- [ ] Context menu: "Report Message" (creates report)
- [ ] Commands:
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
- [ ] Location: `data/federation/`
- [ ] Files:
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
- [ ] `create_federation(name, parent_guild_id) -> str`
- [ ] `join_federation(guild_id, federation_id, invite_key)`
- [ ] `leave_federation(guild_id)`
- [ ] `get_member_tier(guild_id) -> str`
- [ ] `set_member_tier(guild_id, tier)`
- [ ] `check_blocklist(user_id) -> Optional[BlocklistEntry]`
- [ ] `add_to_blocklist(user_id, reason, evidence, guild_id)`
- [ ] `remove_from_blocklist(user_id)`
- [ ] `propagate_action(action_type, data, from_guild_id)`
- [ ] `propagate_verification(user_id, from_guild_id)`
- [ ] `start_vote(federation_id, topic, options, duration)`
- [ ] `cast_vote(vote_id, guild_id, option)`
- [ ] `get_federation_stats(federation_id)`
- [ ] `check_sync_permission(guild_id, action) -> bool`

#### Module: `modules/federation.py`
- [ ] Commands:
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

- [ ] `FederationService.get_cross_server_reputation(user_id) -> float`
- [ ] Aggregate reputation from all federated servers
- [ ] +/- ratings with 2 per 12h limit per user
- [ ] Commands:
  - `rep + @user [reason]`
  - `rep - @user [reason]`
  - `rep view @user`

---

### 3.4 Network Directory

**Depends on:** Portfolio, Federation

- [ ] Opt-in searchable artist directory
- [ ] Syncs: name, specialties, status, price range, tags, portfolio preview
- [ ] Commands:
  - `directory join`
  - `directory leave`
  - `directory search <tags...>`
  - `directory profile [@user]`
  - `directory update` - Sync changes

---

### 3.5 Scammer Database

**Depends on:** Federation

#### Storage: `data/federation/{fed_id}/scammers.json`
- [ ] Track reported scammers with evidence chain
- [ ] Severity levels: low, medium, high, critical
- [ ] Requires confirmation from multiple servers
- [ ] Commands:
  - `scammer check <user>`
  - `scammer report <user> <reason> [evidence_url]`
  - `scammer list`

---

### 3.6 Cross-Server Sync Features

- [ ] **Cross-Server Warnings** - Share warnings for pattern detection
- [ ] **Cross-Server Mute** - Mute across all federated servers
- [ ] **Cross-Server User Notes** - Share mod notes (opt-in)
- [ ] **Network Alerts** - Broadcast urgent alerts (scammer active, raid)
- [ ] **Portfolio Sync** - Portfolio syncs across federated servers user is in
- [ ] **Commission Search** - Search artists by status, price, style across federation

---

## Phase 4: Enhancement Features

### 4.1 Utility Module

**File:** `modules/utility.py`

#### Bookmark System
- [ ] Storage: `data/utility/{user_id}/bookmarks.json`
- [ ] Commands:
  - `bookmark [message_link] [note]`
  - `bookmark list`
  - `bookmark remove <id>`
  - `bookmark delay <message_link> <time>` - Delayed delivery

#### AFK System
- [ ] Storage: `data/utility/{user_id}/afk.json`
- [ ] Commands:
  - `afk [message]`
  - `afk off`
- [ ] Auto-clear on message, paginated mention collection on return

#### Personal Notes
- [ ] Storage: `data/utility/{user_id}/notes.json`
- [ ] Commands:
  - `note add <content>`
  - `notes`
  - `note remove <id>`

#### Command Aliases
- [ ] Storage: `data/guilds/{guild_id}/aliases.json`
- [ ] Commands:
  - `alias add <shortcut> <full_command>`
  - `alias remove <shortcut>`
  - `alias list`

#### Data Export
- [ ] `export` - Export all user data as JSON
- [ ] Include: profile, portfolio, commissions, bookmarks, notes

---

### 4.2 Communication Module

**File:** `modules/communication.py`

#### Anonymous Feedback Box
- [ ] Storage: `data/communication/{guild_id}/feedback.json`
- [ ] Commands:
  - `feedback <message>` - Submit (DM only)
  - `feedbackconfig channel <#channel>`
  - `feedbackconfig <on|off>`

#### Commission Opening Announcements
- [ ] Auto-post when artist opens commissions
- [ ] Include rate card image if available
- [ ] Storage: configured channel per guild

#### Important Message Acknowledgment
- [ ] Storage: `data/communication/{guild_id}/acknowledgments.json`
- [ ] Commands:
  - `tagimportant @user <message_id>`
- [ ] Ping every 6h until checkmark reaction

---

### 4.3 Art & Creative Tools

**File:** `modules/art_tools.py`

#### Color Palette Generator
- [ ] Methods: complementary, analogous, triadic, split-complementary, tetradic, monochromatic
- [ ] Output: JPG via RenderService
- [ ] Commands:
  - `palette <base_color> [method] [count]`
  - `palette random [method] [count]`

#### Art Prompt Roulette
- [ ] Categories: character, landscape, creature, object, scene
- [ ] Difficulty: easy, medium, hard
- [ ] Commands:
  - `artprompt [category] [difficulty]`

#### Art Dice
- [ ] Roll random constraints
- [ ] Commands:
  - `artdice` - Roll all constraints
  - `artdice <constraint_type>`

#### Rate Card Generator
- [ ] Templates: minimal, detailed, colorful, professional
- [ ] Commands:
  - `ratecard [template]`

---

### 4.4 Automation Features

**File:** `services/automation_service.py`

#### Commission Auto-Close/Open
- [ ] Integrated with CommissionService
- [ ] Close when slots fill, reopen when freed

#### Waitlist Auto-Promote
- [ ] Auto-notify next user when slot opens
- [ ] Configurable timeout before moving to next

#### Inactive Commission Cleanup
- [ ] Flag commissions with no updates in X days
- [ ] Scheduled check

#### Vacation Mode
- [ ] Storage: `data/commissions/{guild_id}/{user_id}/vacation.json`
- [ ] Commands:
  - `vacation start <return_date> [message]`
  - `vacation end`
- [ ] Auto-close commissions, show return date, pause deadlines

#### Trigger Chains
- [ ] Storage: `data/automation/{guild_id}/triggers.json`
- [ ] When event X → execute action Y
- [ ] Commands:
  - `trigger add <event> <action>`
  - `trigger remove <id>`
  - `trigger list`

#### Scheduled Actions
- [ ] Schedule future role changes, messages, permission changes
- [ ] Commands:
  - `schedule role add @user @role <time>`
  - `schedule role remove @user @role <time>`
  - `schedule message <#channel> <time> <content>`
  - `schedule list`
  - `schedule cancel <id>`

---

### 4.5 Role Management

**File:** `modules/roles.py`

#### Temporary Roles
- [ ] Auto-expire after duration
- [ ] Commands:
  - `temprole @user @role <duration>`
  - `temprole list`

#### Role Request System
- [ ] Users request, mods approve/deny
- [ ] Commands:
  - `rolerequest <role>`
  - `rolerequest list` (mod)
  - `rolerequest approve <id>` (mod)
  - `rolerequest deny <id> [reason]` (mod)

#### Role Bundles
- [ ] Assign multiple roles at once
- [ ] Commands:
  - `rolebundle create <name> <role1, role2, ...>`
  - `rolebundle apply @user <bundle_name>`
  - `rolebundle list`

#### Reaction Roles
- [ ] Assign roles via reactions
- [ ] Mutually exclusive groups
- [ ] Commands:
  - `reactionrole setup <message_id>`
  - `reactionrole add <emoji> @role`
  - `reactionrole exclusive <group_name> <emoji1, emoji2, ...>`

---

### 4.6 Custom Content

#### Custom Commands
- [ ] Storage: `data/guilds/{guild_id}/custom_commands.json`
- [ ] Commands:
  - `customcmd add <trigger> <response>`
  - `customcmd remove <trigger>`
  - `customcmd list`

#### Auto-Responder Enhancements
- [ ] Extend existing auto_responder module
- [ ] Add: regex support, cooldowns, role restrictions, random responses

#### Form Builder
- [ ] Multi-question forms for applications/signups
- [ ] Commands:
  - `form create <name>`
  - `form addquestion <form_name> <question>`
  - `form publish <form_name> <#channel>`
  - `form responses <form_name>`

---

## Phase 5: Analytics & Web UI

### 5.1 Analytics System

**File:** `services/analytics_service.py`

#### Storage: `data/analytics/{guild_id}/`
- [ ] `stats.json` - Aggregate stats
- [ ] `timeseries/` - Time-bucketed data

#### Metrics to Track
- [ ] Commission stats: completed, value, avg completion time, by month, by type
- [ ] Profile stats: views, portfolio views by entry, views by week
- [ ] Federation stats: synced actions, blocklist hits, reputation avg
- [ ] Bot stats: uptime, commands run, messages scanned

#### Service Functions
- [ ] `record_event(guild_id, event_type, data)`
- [ ] `get_commission_stats(guild_id, period) -> dict`
- [ ] `get_profile_stats(user_id) -> dict`
- [ ] `get_federation_stats(federation_id) -> dict`
- [ ] `get_bot_stats() -> dict`
- [ ] `calculate_trends(guild_id, metric) -> list`

#### Commands
- [ ] `stats commissions [period]`
- [ ] `stats profile`
- [ ] `stats federation`
- [ ] `stats bot`
- [ ] `stats trends <metric>`

---

### 5.2 Web UI Infrastructure

**Directory:** `web/`

#### Technology Stack
- [ ] Backend: aiohttp (consistent with existing async patterns)
- [ ] Frontend: Vanilla HTML/CSS/JS ("old internet aesthetic")
- [ ] Auth: Discord OAuth2
- [ ] No frameworks

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
- [ ] Server overview, quick stats, recent activity
- [ ] Route: `GET /admin/{guild_id}/`

#### Modules
- [ ] Enable/disable modules, per-module settings
- [ ] Route: `GET/POST /admin/{guild_id}/modules`

#### Moderation Settings
- [ ] Warning thresholds, escalation paths, auto-mod config
- [ ] Route: `GET/POST /admin/{guild_id}/moderation`

#### Auto-Responders
- [ ] Create/edit/delete, test patterns
- [ ] Route: `GET/POST /admin/{guild_id}/autoresponders`

#### Custom Commands
- [ ] Manage commands, view usage
- [ ] Route: `GET/POST /admin/{guild_id}/commands`

#### Forms
- [ ] Build forms, view submissions
- [ ] Route: `GET/POST /admin/{guild_id}/forms`

#### Roles
- [ ] Reaction roles, requests queue, temp roles, bundles
- [ ] Route: `GET/POST /admin/{guild_id}/roles`

#### Automation
- [ ] Trigger chains, scheduled actions
- [ ] Route: `GET/POST /admin/{guild_id}/automation`

#### Commission Settings
- [ ] Stages, templates, defaults
- [ ] Route: `GET/POST /admin/{guild_id}/commissions`

#### Logs
- [ ] Mod logs, action history, filterable
- [ ] Route: `GET /admin/{guild_id}/logs`

#### Bot Persona
- [ ] Customize name/avatar/style
- [ ] Route: `GET/POST /admin/{guild_id}/persona`

---

### 5.4 Federation Admin Panel

- [ ] Dashboard - overview, health, member count
- [ ] Member Servers - manage, trust scores, sync status
- [ ] Trust Network - visualize relationships
- [ ] Sync Settings - what syncs, from which tiers
- [ ] Cross-Post Channels - art-share configs
- [ ] Blocklist - manage federation blocklist
- [ ] Audit Log - sync events, membership changes
- [ ] Announcements - push to children
- [ ] Applications - review join requests

---

### 5.5 Bot Owner Panel

- [ ] Dashboard - global stats, all servers, uptime
- [ ] Servers - list, per-server controls, leave
- [ ] Federation Management - all federations overview
- [ ] AI Checker Module:
  - Pricing (cost per use, bulk discounts, free tier)
  - Server Credits (view/adjust, transactions)
  - Usage Stats (per-server, revenue)
  - Enable/Disable per server
  - Rate Limits
  - Billing integration
- [ ] Global Settings - defaults, feature flags
- [ ] Maintenance - restart, update, backup
- [ ] Logs - global errors, performance

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
