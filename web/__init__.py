"""
Web UI package for Discord bot administration.

This package provides a web-based interface for managing the bot,
with an "old internet aesthetic" design.
"""

from web.server import WebServer, run_web_server

__all__ = ['WebServer', 'run_web_server']
