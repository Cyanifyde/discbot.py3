"""
Bot owner panel routes.
"""
from aiohttp import web
from web.auth import require_auth
from services.analytics_service import AnalyticsService


def setup_routes(app, bot):
    """Set up owner panel routes."""
    app.router.add_get('/owner', handle_dashboard)
    app.router.add_get('/owner/servers', handle_servers)
    app.router.add_post('/owner/servers', handle_servers_post)
    app.router.add_get('/owner/ai_checker', handle_ai_checker)
    app.router.add_post('/owner/ai_checker', handle_ai_checker_post)
    app.router.add_get('/owner/settings', handle_settings)
    app.router.add_post('/owner/settings', handle_settings_post)
    app.router.add_get('/owner/maintenance', handle_maintenance)
    app.router.add_post('/owner/maintenance', handle_maintenance_post)
    app.router.add_get('/owner/logs', handle_logs)


@require_auth('owner')
async def handle_dashboard(request):
    """Bot owner dashboard."""
    bot = request.app['bot']
    analytics = AnalyticsService()
    stats = analytics.get_bot_stats()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Owner Dashboard</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Bot Owner Dashboard</h1>
                <nav>
                    <a href="/owner/servers">Servers</a>
                    <a href="/owner/ai_checker">AI Checker</a>
                    <a href="/owner/settings">Settings</a>
                    <a href="/owner/maintenance">Maintenance</a>
                    <a href="/owner/logs">Logs</a>
                </nav>
            </header>
            <main>
                <section class="stats-grid">
                    <div class="stat-card">
                        <h3>Total Servers</h3>
                        <p class="stat-value">{len(bot.guilds)}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Total Users</h3>
                        <p class="stat-value">{sum(g.member_count for g in bot.guilds)}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Commands Run</h3>
                        <p class="stat-value">{stats.get("total_commands_run", 0):,}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Uptime</h3>
                        <p class="stat-value">{stats.get("uptime_seconds", 0) // 3600}h</p>
                    </div>
                </section>

                <section>
                    <h2>Global Statistics</h2>
                    <table class="stats-table">
                        <tr>
                            <th>Metric</th>
                            <th>Value</th>
                        </tr>
                        <tr>
                            <td>Messages Scanned</td>
                            <td>{stats.get("total_messages_scanned", 0):,}</td>
                        </tr>
                        <tr>
                            <td>Guilds Tracked</td>
                            <td>{stats.get("guilds_tracked", 0)}</td>
                        </tr>
                    </table>
                </section>

                <section>
                    <h2>Quick Actions</h2>
                    <div class="button-grid">
                        <a href="/owner/servers" class="button">Manage Servers</a>
                        <a href="/owner/ai_checker" class="button">AI Checker Module</a>
                        <a href="/owner/settings" class="button">Global Settings</a>
                        <a href="/owner/maintenance" class="button">Maintenance</a>
                        <a href="/owner/logs" class="button">View Logs</a>
                    </div>
                </section>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('owner')
async def handle_servers(request):
    """Server management page."""
    bot = request.app['bot']

    servers_html = '\n'.join([f'''
        <tr>
            <td>{html.escape(guild.name)}</td>
            <td>{guild.id}</td>
            <td>{guild.member_count}</td>
            <td>
                <form method="POST" style="display:inline;" onsubmit="return confirm('Leave server?');">
                    <input type="hidden" name="action" value="leave">
                    <input type="hidden" name="guild_id" value="{guild.id}">
                    <button type="submit" class="button danger">Leave</button>
                </form>
            </td>
        </tr>
    ''' for guild in sorted(bot.guilds, key=lambda g: g.member_count, reverse=True)])

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Server Management</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Server Management</h1>
                <a href="/owner" class="button">← Back to Dashboard</a>
            </header>
            <main>
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Server Name</th>
                            <th>ID</th>
                            <th>Members</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {servers_html}
                    </tbody>
                </table>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('owner')
async def handle_servers_post(request):
    """Handle server actions."""
    data = await request.post()
    action = data.get('action')
    guild_id = data.get('guild_id')

    if action == 'leave' and guild_id:
        try:
            bot = request.app['bot']
            guild = bot.get_guild(int(guild_id))
            if guild:
                await guild.leave()
        except (ValueError, TypeError) as e:
            return web.Response(text=f'Invalid guild_id: {e}', status=400)

    return web.HTTPFound('/owner/servers')


@require_auth('owner')
async def handle_ai_checker(request):
    """AI Checker module management."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>AI Checker Module</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>AI Checker Module</h1>
                <a href="/owner" class="button">← Back to Dashboard</a>
            </header>
            <main>
                <section>
                    <h2>Pricing Configuration</h2>
                    <p>Cost per use, bulk discounts, free tier settings (TODO)</p>
                </section>

                <section>
                    <h2>Server Credits</h2>
                    <p>View/adjust credits, transaction history (TODO)</p>
                </section>

                <section>
                    <h2>Usage Statistics</h2>
                    <p>Per-server usage, revenue stats (TODO)</p>
                </section>

                <section>
                    <h2>Global Settings</h2>
                    <p>Enable/disable per server, rate limits (TODO)</p>
                </section>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('owner')
async def handle_ai_checker_post(request):
    """Handle AI Checker settings updates."""
    return web.HTTPFound('/owner/ai_checker')


@require_auth('owner')
async def handle_settings(request):
    """Global settings page."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Global Settings</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Global Settings</h1>
                <a href="/owner" class="button">← Back to Dashboard</a>
            </header>
            <main>
                <p>Global defaults, feature flags (TODO)</p>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('owner')
async def handle_settings_post(request):
    """Handle global settings updates."""
    return web.HTTPFound('/owner/settings')


@require_auth('owner')
async def handle_maintenance(request):
    """Maintenance tools page."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Maintenance</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Maintenance</h1>
                <a href="/owner" class="button">← Back to Dashboard</a>
            </header>
            <main>
                <section>
                    <h2>Bot Control</h2>
                    <form method="POST">
                        <button type="submit" name="action" value="restart" class="button warning">Restart Bot</button>
                        <button type="submit" name="action" value="backup" class="button">Create Backup</button>
                    </form>
                </section>

                <section>
                    <h2>Database Maintenance</h2>
                    <p>Cleanup, optimization tools (TODO)</p>
                </section>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('owner')
async def handle_maintenance_post(request):
    """Handle maintenance actions."""
    data = await request.post()
    action = data.get('action')

    if action == 'restart':
        # TODO: Implement graceful restart
        pass
    elif action == 'backup':
        # TODO: Implement backup creation
        pass

    return web.HTTPFound('/owner/maintenance')


@require_auth('owner')
async def handle_logs(request):
    """Global logs viewer."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Global Logs</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Global Logs</h1>
                <a href="/owner" class="button">← Back to Dashboard</a>
            </header>
            <main>
                <p>Global error logs, performance metrics (TODO)</p>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')
