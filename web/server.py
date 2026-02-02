"""
Web UI server for Discord bot administration.
Uses aiohttp for async web serving.
"""
import os
import asyncio
from aiohttp import web
from pathlib import Path
import logging

from web.auth import setup_auth, require_auth
from web.routes import admin, owner

logger = logging.getLogger(__name__)


class WebServer:
    """Web server for bot administration panels."""

    def __init__(self, bot, host='127.0.0.1', port=8080):
        """
        Initialize web server.

        Args:
            bot: Discord bot instance
            host: Host to bind to
            port: Port to listen on
        """
        self.bot = bot
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner = None

        # Set up routes
        self._setup_routes()

        # Set up authentication
        setup_auth(self.app, bot)

    def _setup_routes(self):
        """Set up all web routes."""
        # Static files
        static_path = Path(__file__).parent / 'static'
        self.app.router.add_static('/static/', path=static_path, name='static')

        # Home/landing page
        self.app.router.add_get('/', self.handle_index)

        # Admin routes
        admin.setup_routes(self.app, self.bot)

        # Owner routes
        owner.setup_routes(self.app, self.bot)

    async def handle_index(self, request):
        """Handle index/landing page."""
        html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Discord Bot Admin</title>
            <link rel="stylesheet" href="/static/style.css">
        </head>
        <body>
            <div class="container">
                <header>
                    <h1>Discord Bot Administration</h1>
                    <p class="subtitle">Old Internet Aesthetic Edition</p>
                </header>

                <main>
                    <section class="panel-grid">
                        <div class="panel">
                            <h2>🛠️ Server Admin</h2>
                            <p>Manage your server's bot settings, modules, and moderation</p>
                            <a href="/admin" class="button">Enter Admin Panel</a>
                        </div>

                        <div class="panel">
                            <h2>👑 Bot Owner</h2>
                            <p>Global bot management and configuration</p>
                            <a href="/owner" class="button">Enter Owner Panel</a>
                        </div>
                    </section>

                    <section class="info">
                        <h3>Features</h3>
                        <ul>
                            <li>Server administration and module management</li>
                            <li>Moderation tools and auto-mod configuration</li>
                            <li>Commission system management</li>
                            <li>Analytics and statistics</li>
                            <li>Custom commands and automation</li>
                        </ul>
                    </section>
                </main>

                <footer>
                    <p>Discord Bot Admin Panel v1.0 | <a href="https://github.com">Documentation</a></p>
                </footer>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')

    async def start(self):
        """Start the web server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logger.info(f"Web UI started at http://{self.host}:{self.port}")

    async def stop(self):
        """Stop the web server."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("Web UI stopped")


async def run_web_server(bot, host='127.0.0.1', port=8080):
    """
    Run the web server.

    Args:
        bot: Discord bot instance
        host: Host to bind to
        port: Port to listen on
    """
    server = WebServer(bot, host, port)
    await server.start()
    return server
