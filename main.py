"""
Main entry point for the Discord bot.

Loads configuration from environment and starts the bot.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from discord.errors import PrivilegedIntentsRequired

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# Import bot after .env is loaded so modules can read env vars at import time.
from bot import DiscBot

# Get configuration from environment
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("discbot")
logging.getLogger().setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
logging.getLogger("discord").setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
logging.getLogger("asyncio").setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
logging.getLogger("fontTools").setLevel(logging.WARNING)
logging.getLogger("weasyprint").setLevel(logging.WARNING)

# Suppress verbose third-party library logs unless LOG_LEVEL is DEBUG
if LOG_LEVEL.upper() != "DEBUG":
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.WARNING)
    logging.getLogger("pdf2image").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

# Debug: show if .env was found
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
    except Exception as e:
        logger.error("Failed to start bot: %s", e)
        if "401" in str(e) or "Improper token" in str(e):
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
