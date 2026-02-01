# Discord Bot

A modular Discord bot with auto-responder, profile management, and content moderation features.

## 🚀 Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your bot token
   ```

3. **Run the bot:**
   ```bash
   python main.py
   ```

## 📁 Project Structure

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
│   ├── auto_responder.py   # Compat layer → responders/
│   └── dm_sender.py        # DM sending logic
│
└── config.guild/           # Per-guild configurations
    └── template.*.json     # Config templates
```

## 🏗️ Architecture

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

## 🔧 Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DISCORD_BOT_TOKEN` | Your bot token | Yes |
| `OWNER_ID` | Bot owner's Discord ID | No |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, etc.) | No |

### Guild Configuration

Each guild has a config file in `config.guild/{guild_id}.json`:

```json
{
  "guild_id": 123456789,
  "unverified_role_id": null,
  "action_log_channel_id": null,
  "exempt_role_ids": [],
  "ignored_channel_ids": []
}
```

## 📝 Adding New Features

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

## 🧪 Testing

Services can be tested without Discord:

```python
from services.hash_checker import HashChecker

def test_hash_checker():
    checker = HashChecker({"abc123", "def456"})
    assert checker.check("abc123") == True
    assert checker.check("unknown") == False
```

## 📄 License

MIT
