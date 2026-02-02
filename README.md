# Discord Bot

A modular Discord bot with auto-responder, verification, and moderation utilities (scanner + inactivity enforcement).

## Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your bot token
   ```

3. **Enable privileged intents (Discord Developer Portal):**
   - Bot → **Privileged Gateway Intents**
     - Enable **SERVER MEMBERS INTENT** (members)
     - Enable **MESSAGE CONTENT INTENT** (message content)

4. **Run the bot:**
   ```bash
   python main.py
   ```

## Project Structure

```
discbot/
├── main.py                 # Entry point
├── .env                    # Secrets (gitignored)
├── .env.example            # Template for secrets
│
├── bot/                    # Discord client layer
│   ├── client.py           # Main DiscBot class
│   └── guild_state.py      # Per-guild state management
│
├── services/               # Business logic (Discord-independent)
│   ├── scanner.py          # Scanner commands + persisted state
│   ├── inactivity.py       # Inactivity commands + persisted state
│   ├── enforcement.py      # Role removal, unverified assignment
│   ├── job_factory.py      # Create scan jobs from messages
│   └── hash_checker.py     # Image hash matching
│
├── core/                   # Infrastructure
│   ├── config.py           # Config loading/validation
│   ├── constants.py        # Config keys, enums
│   ├── types.py            # Dataclasses (ScanJob, UserRecord, etc.)
│   ├── storage.py          # Persistent user record storage
│   ├── queueing.py         # Job queue system
│   ├── io_utils.py         # File I/O helpers
│   └── utils.py            # General utilities
│
├── responders/             # Auto-responder system
│   ├── engine.py           # Main orchestration
│   ├── matching.py         # Trigger matching logic
│   ├── config_loader.py    # Guild config loading
│   └── delivery.py         # Response sending
│
├── classes/                # Responder handlers
│   ├── response_handlers.py # Base classes
│   ├── profile.py          # Profile commands
│   └── reminder.py         # Reminder functionality
│
├── modules/                # Optional features
│   ├── auto_responder.py   # Auto-responder (commands + runtime)
│   ├── verification.py     # Verification buttons (add/remove/restore)
│   ├── modules_command.py  # Per-guild module enable + permissions
│   └── dm_sender.py        # Owner-only DM send utility
│
└── config.guild/           # Per-guild configurations
    └── template.*.json     # Config templates
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

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DISCORD_BOT_TOKEN` | Your bot token | Yes |
| `OWNER_ID` | Bot owner's Discord ID (used for owner-only DM commands) | No |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, etc.) | No |

### Guild Configuration

Each guild has a config file in `config.guild/{guild_id}.json`.

- New guilds: config is auto-created when the bot joins the server.
- Existing guilds: if a config is missing, you can create it by copying `config.default.json` and setting `guild_id`.

```json
{
  "guild_id": 123456789,
  "unverified_role_id": null,
  "action_log_channel_id": null,
  "exempt_role_ids": [],
  "ignored_channel_ids": []
}
```

### Module Management + Help

- In any server channel: mention the bot and say `help` (example: `@YourBot help`) to see all registered modules/commands.
- Admins can control modules and permissions per guild with:
  - `modules list`
  - `modules enable <module>` / `modules disable <module>`
  - `modules allow <module|command> <role_id>` / `modules deny <module|command> <role_id>`

Available modules: `scanner`, `inactivity`, `verification`, `autoresponder`.

### Auto-Responder Config

Auto-responder triggers are stored per guild in `config.guild/{guild_id}.autoresponder.json`.
User-added triggers can be managed via text commands:

- `addresponse "trigger" "response"`
- `listresponses`
- `removeresponse "trigger"`

## Adding New Features

### New Responder Handler

1. Create a class in `classes/`:
```python
from classes.response_handlers import BaseResponder, ResponderInput

class MyHandler(BaseResponder):
    async def run(self, payload: ResponderInput) -> str:
        return f"Hello, {payload.message.author.name}!"
```

2. Reference in guild config:
```json
{
  "triggers": {
    "!hello": {
      "handler": "classes.my_module:MyHandler"
    }
  }
}
```

### New Service

1. Create in `services/`:
```python
class MyService:
    def __init__(self, config: dict):
        self.config = config
    
    def do_something(self) -> str:
        return "Done!"
```

2. Add to `GuildState` in `bot/guild_state.py`

## Testing

Services can be tested without Discord:

```python
from services.hash_checker import HashChecker

def test_hash_checker():
    checker = HashChecker({"abc123", "def456"})
    assert checker.check("abc123") == True
    assert checker.check("unknown") == False
```

## License

MIT
