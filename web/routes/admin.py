"""
Server admin panel routes.
"""
from aiohttp import web
from web.auth import require_auth
from pathlib import Path
import json


def setup_routes(app, bot):
    """Set up admin panel routes."""
    app.router.add_get('/admin', handle_admin_index)
    app.router.add_get('/admin/{guild_id}/', handle_dashboard)
    app.router.add_get('/admin/{guild_id}/modules', handle_modules)
    app.router.add_post('/admin/{guild_id}/modules', handle_modules_post)
    app.router.add_get('/admin/{guild_id}/moderation', handle_moderation)
    app.router.add_post('/admin/{guild_id}/moderation', handle_moderation_post)
    app.router.add_get('/admin/{guild_id}/autoresponders', handle_autoresponders)
    app.router.add_post('/admin/{guild_id}/autoresponders', handle_autoresponders_post)
    app.router.add_get('/admin/{guild_id}/commands', handle_commands)
    app.router.add_post('/admin/{guild_id}/commands', handle_commands_post)
    app.router.add_get('/admin/{guild_id}/forms', handle_forms)
    app.router.add_post('/admin/{guild_id}/forms', handle_forms_post)
    app.router.add_get('/admin/{guild_id}/roles', handle_roles)
    app.router.add_post('/admin/{guild_id}/roles', handle_roles_post)
    app.router.add_get('/admin/{guild_id}/automation', handle_automation)
    app.router.add_post('/admin/{guild_id}/automation', handle_automation_post)
    app.router.add_get('/admin/{guild_id}/commissions', handle_commissions)
    app.router.add_post('/admin/{guild_id}/commissions', handle_commissions_post)
    app.router.add_get('/admin/{guild_id}/logs', handle_logs)
    app.router.add_get('/admin/{guild_id}/persona', handle_persona)
    app.router.add_post('/admin/{guild_id}/persona', handle_persona_post)


@require_auth()
async def handle_admin_index(request):
    """Show guild selection page."""
    user = request['user']
    bot = request.app['bot']

    # Get user's guilds where they're admin
    admin_guilds = []
    for guild_data in user.get('guilds', []):
        guild_id = int(guild_data['id'])
        guild = bot.get_guild(guild_id)

        if guild:
            # Check if user is admin
            member = guild.get_member(int(user['user_id']))
            if member and member.guild_permissions.administrator:
                admin_guilds.append({
                    'id': guild_id,
                    'name': guild.name,
                    'icon': guild.icon.url if guild.icon else None,
                    'member_count': guild.member_count
                })

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Server Admin - Select Server</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Server Administration</h1>
                <p>Select a server to manage</p>
            </header>
            <main>
                <div class="guild-grid">
                    {''.join([f'''
                    <div class="guild-card">
                        <h3>{g["name"]}</h3>
                        <p>{g["member_count"]} members</p>
                        <a href="/admin/{g["id"]}/" class="button">Manage</a>
                    </div>
                    ''' for g in admin_guilds])}
                </div>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_dashboard(request):
    """Server admin dashboard."""
    guild_id = request.match_info['guild_id']
    bot = request.app['bot']
    guild = bot.get_guild(int(guild_id))

    if not guild:
        return web.Response(text='Guild not found', status=404)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{guild.name} - Dashboard</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>{guild.name} - Admin Dashboard</h1>
                <nav>
                    <a href="/admin/{guild_id}/modules">Modules</a>
                    <a href="/admin/{guild_id}/moderation">Moderation</a>
                    <a href="/admin/{guild_id}/commissions">Commissions</a>
                    <a href="/admin/{guild_id}/logs">Logs</a>
                </nav>
            </header>
            <main>
                <section class="stats-grid">
                    <div class="stat-card">
                        <h3>Members</h3>
                        <p class="stat-value">{guild.member_count}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Channels</h3>
                        <p class="stat-value">{len(guild.channels)}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Roles</h3>
                        <p class="stat-value">{len(guild.roles)}</p>
                    </div>
                </section>

                <section>
                    <h2>Quick Actions</h2>
                    <div class="button-grid">
                        <a href="/admin/{guild_id}/modules" class="button">Configure Modules</a>
                        <a href="/admin/{guild_id}/moderation" class="button">Moderation Settings</a>
                        <a href="/admin/{guild_id}/autoresponders" class="button">Auto-Responders</a>
                        <a href="/admin/{guild_id}/commands" class="button">Custom Commands</a>
                        <a href="/admin/{guild_id}/forms" class="button">Forms</a>
                        <a href="/admin/{guild_id}/roles" class="button">Role Management</a>
                        <a href="/admin/{guild_id}/automation" class="button">Automation</a>
                        <a href="/admin/{guild_id}/commissions" class="button">Commission Settings</a>
                        <a href="/admin/{guild_id}/logs" class="button">View Logs</a>
                        <a href="/admin/{guild_id}/persona" class="button">Bot Persona</a>
                    </div>
                </section>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_modules(request):
    """Module management page."""
    guild_id = request.match_info['guild_id']
    # TODO: Load module configuration
    html = "<h1>Module Management (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_modules_post(request):
    """Handle module configuration updates."""
    # TODO: Save module configuration
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/modules")


@require_auth('admin')
async def handle_moderation(request):
    """Moderation settings page."""
    html = "<h1>Moderation Settings (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_moderation_post(request):
    """Handle moderation settings updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/moderation")


@require_auth('admin')
async def handle_autoresponders(request):
    """Auto-responders management page."""
    html = "<h1>Auto-Responders (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_autoresponders_post(request):
    """Handle auto-responder updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/autoresponders")


@require_auth('admin')
async def handle_commands(request):
    """Custom commands management page."""
    html = "<h1>Custom Commands (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_commands_post(request):
    """Handle custom command updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/commands")


@require_auth('admin')
async def handle_forms(request):
    """Forms builder page."""
    html = "<h1>Form Builder (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_forms_post(request):
    """Handle form updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/forms")


@require_auth('admin')
async def handle_roles(request):
    """Role management page."""
    html = "<h1>Role Management (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_roles_post(request):
    """Handle role configuration updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/roles")


@require_auth('admin')
async def handle_automation(request):
    """Automation settings page."""
    html = "<h1>Automation (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_automation_post(request):
    """Handle automation updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/automation")


@require_auth('admin')
async def handle_commissions(request):
    """Commission settings page."""
    html = "<h1>Commission Settings (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_commissions_post(request):
    """Handle commission settings updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/commissions")


@require_auth('admin')
async def handle_logs(request):
    """Logs viewer page."""
    html = "<h1>Logs (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_persona(request):
    """Bot persona customization page."""
    html = "<h1>Bot Persona (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth('admin')
async def handle_persona_post(request):
    """Handle persona updates."""
    return web.HTTPFound(f"/admin/{request.match_info['guild_id']}/persona")
