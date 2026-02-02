# Discord Bot

Modular Discord bot focused on moderation, commissions, portfolios, federation, trust, automation, and utilities.

## Feature Overview

- Moderation: warnings, mutes, bans, kicks, and mod notes, with optional cross-server sync.
- Server Link and Sync: parent/child links, upstream approvals, and sync protection.
- Federation: create/join/leave federations, invites, member tiers, blocklist, voting, settings, audit, announcements, and stats.
- Trust and Vouching: trust scores, tiers, vouching, history, and permission checks.
- Commissions: create and manage commissions with stages, waitlist, deadlines, tags, revisions, blacklist, invoices, contracts, payment confirmation, search, and summary.
- Portfolio: add/remove entries, tags, categories, featured entries, privacy controls, reorder, before/after, batch add, and stats.
- Reports: user reports with assignment, resolution, dismissal, and stats.
- Roles: temporary roles, role requests and approvals, bundles, and reaction roles.
- Communication: feedback box, announcements and subscriptions, acknowledgment tracking.
- Auto-responder: per-guild triggers with text and embed responses.
- Verification: role-based verification via buttons with persistence across restarts.
- Utility: bookmarks, AFK, personal notes, aliases, and data export.
- Art tools: palettes, prompts, art dice, and rate card rendering.
- Custom content: custom commands and forms.
- Automation: triggers, schedules, and vacation mode.
- Server stats: server statistics views.
- Owner utilities: DM sender and module management commands.

## Project Structure

```
discbot/
├── main.py                 # Entry point
├── bot/                    # Discord client layer
│   ├── client.py           # Main DiscBot class
│   └── guild_state.py      # Per-guild state management
├── core/                   # Infrastructure, config, storage, permissions
├── services/               # Business logic and background services
├── modules/                # Optional feature modules (commands + runtime)
├── responders/             # Auto-responder engine
├── classes/                # Responder handlers and profile/reminder logic
├── templates/              # Render templates (rate cards, invoices, palettes)
├── config.guild/           # Per-guild configurations
├── profiles/               # Profile data
├── portfolios/             # Portfolio data
└── images/                 # Generated or cached images
```

## Architecture

### Layered Design

```
┌─────────────────────────────────────────┐
│           Discord Events                │  ← bot/client.py
├─────────────────────────────────────────┤
│         Guild State Management          │  ← bot/guild_state.py
├─────────────────────────────────────────┤
│         Services (Business Logic)       │  ← services/
├─────────────────────────────────────────┤
│         Core Infrastructure             │  ← core/
└─────────────────────────────────────────┘
```

### Key Principles

1. **Separation of Concerns**: Discord API code is isolated in `bot/`, business logic in `services/`
2. **Type Safety**: Dataclasses in `core/types.py` instead of raw dicts
3. **Testability**: Services have no Discord dependencies, easy to unit test
4. **Configuration**: All keys defined in `core/constants.py` - no magic strings
