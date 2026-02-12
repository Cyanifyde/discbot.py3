"""
Stable runtime entrypoint for starting the Discord bot.

`main.py` should remain a thin bootstrap that delegates to this module.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from discord.errors import PrivilegedIntentsRequired

from bot import DiscBot

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
_log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("discbot")
logging.getLogger().setLevel(_log_level)
logging.getLogger("discord").setLevel(_log_level)
logging.getLogger("asyncio").setLevel(_log_level)

if _log_level > logging.DEBUG:
    logging.getLogger("PIL").setLevel(logging.WARNING)
else:
    logging.getLogger("PIL").setLevel(_log_level)

if not env_path.exists():
    logger.warning(".env file not found at %s", env_path)


async def main() -> None:
    token = BOT_TOKEN
    if not token:
        logger.error("Missing bot token. Set DISCORD_BOT_TOKEN in .env or environment.")
        return

    bot = DiscBot()
    try:
        await bot.start(token)
    except PrivilegedIntentsRequired:
        logger.error(
            "Privileged intents required. Enable MESSAGE CONTENT and SERVER MEMBERS intents "
            "in the Discord developer portal, or disable those intents in code/config if you "
            "intend to run without them."
        )
        try:
            await bot.close()
        except Exception:
            pass
    except Exception as exc:
        logger.error("Failed to start bot: %s", exc)
        if "401" in str(exc) or "Improper token" in str(exc):
            logger.error(
                "Token is invalid. Go to https://discord.com/developers/applications, "
                "select your bot, go to Bot tab, and click 'Reset Token' to generate a new one. "
                "Then update DISCORD_BOT_TOKEN in your .env file."
            )
        try:
            await bot.close()
        except Exception:
            pass
        raise
