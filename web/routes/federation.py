"""
Federation admin panel routes.
"""
from aiohttp import web
from web.auth import require_auth
from services.federation_service import FederationService


def setup_routes(app, bot):
    """Set up federation panel routes."""
    app.router.add_get('/federation', handle_federation_index)
    app.router.add_get('/federation/{fed_id}/', handle_dashboard)
    app.router.add_get('/federation/{fed_id}/members', handle_members)
    app.router.add_post('/federation/{fed_id}/members', handle_members_post)
    app.router.add_get('/federation/{fed_id}/trust', handle_trust_network)
    app.router.add_get('/federation/{fed_id}/sync', handle_sync_settings)
    app.router.add_post('/federation/{fed_id}/sync', handle_sync_post)
    app.router.add_get('/federation/{fed_id}/blocklist', handle_blocklist)
    app.router.add_post('/federation/{fed_id}/blocklist', handle_blocklist_post)
    app.router.add_get('/federation/{fed_id}/audit', handle_audit)
    app.router.add_get('/federation/{fed_id}/announcements', handle_announcements)
    app.router.add_post('/federation/{fed_id}/announcements', handle_announcements_post)
    app.router.add_get('/federation/{fed_id}/applications', handle_applications)
    app.router.add_post('/federation/{fed_id}/applications', handle_applications_post)


@require_auth()
async def handle_federation_index(request):
    """Show federation selection page."""
    user = request['user']
    bot = request.app['bot']
    fed_service = FederationService()

    # Get federations where user has admin access
    admin_federations = []
    for guild_data in user.get('guilds', []):
        guild_id = int(guild_data['id'])
        guild = bot.get_guild(guild_id)

        if guild:
            member = guild.get_member(int(user['user_id']))
            if member and member.guild_permissions.administrator:
                # Check if this guild has a federation
                fed_id = fed_service.get_guild_federation(guild_id)
                if fed_id:
                    fed_info = fed_service.get_federation(fed_id)
                    if fed_info and fed_info['parent_guild_id'] == guild_id:
                        admin_federations.append(fed_info)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Federation Admin</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>Federation Administration</h1>
                <p>Select a federation to manage</p>
            </header>
            <main>
                <div class="fed-grid">
                    {''.join([f'''
                    <div class="fed-card">
                        <h3>{f["name"]}</h3>
                        <p>ID: {f["id"]}</p>
                        <a href="/federation/{f["id"]}/" class="button">Manage</a>
                    </div>
                    ''' for f in admin_federations])}
                </div>
                {'' if admin_federations else '<p>No federations found. Create one from Discord!</p>'}
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_dashboard(request):
    """Federation dashboard."""
    fed_id = request.match_info['fed_id']
    fed_service = FederationService()
    fed_info = fed_service.get_federation(fed_id)

    if not fed_info:
        return web.Response(text='Federation not found', status=404)

    stats = fed_service.get_federation_stats(fed_id)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{fed_info["name"]} - Dashboard</title>
        <link rel="stylesheet" href="/static/style.css">
    </head>
    <body>
        <div class="container">
            <header>
                <h1>{fed_info["name"]} - Federation Dashboard</h1>
                <nav>
                    <a href="/federation/{fed_id}/members">Members</a>
                    <a href="/federation/{fed_id}/trust">Trust Network</a>
                    <a href="/federation/{fed_id}/sync">Sync Settings</a>
                    <a href="/federation/{fed_id}/blocklist">Blocklist</a>
                    <a href="/federation/{fed_id}/audit">Audit Log</a>
                </nav>
            </header>
            <main>
                <section class="stats-grid">
                    <div class="stat-card">
                        <h3>Member Servers</h3>
                        <p class="stat-value">{stats.get("member_count", 0)}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Total Users</h3>
                        <p class="stat-value">{stats.get("total_users", 0)}</p>
                    </div>
                    <div class="stat-card">
                        <h3>Avg Trust Score</h3>
                        <p class="stat-value">{stats.get("avg_trust_score", 0):.1f}</p>
                    </div>
                </section>

                <section>
                    <h2>Quick Actions</h2>
                    <div class="button-grid">
                        <a href="/federation/{fed_id}/members" class="button">Manage Members</a>
                        <a href="/federation/{fed_id}/blocklist" class="button">Blocklist</a>
                        <a href="/federation/{fed_id}/announcements" class="button">Announcements</a>
                        <a href="/federation/{fed_id}/applications" class="button">Applications</a>
                    </div>
                </section>
            </main>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_members(request):
    """Member management page."""
    html = "<h1>Member Management (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_members_post(request):
    """Handle member updates."""
    return web.HTTPFound(f"/federation/{request.match_info['fed_id']}/members")


@require_auth()
async def handle_trust_network(request):
    """Trust network visualization page."""
    html = "<h1>Trust Network (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_sync_settings(request):
    """Sync settings page."""
    html = "<h1>Sync Settings (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_sync_post(request):
    """Handle sync settings updates."""
    return web.HTTPFound(f"/federation/{request.match_info['fed_id']}/sync")


@require_auth()
async def handle_blocklist(request):
    """Blocklist management page."""
    html = "<h1>Blocklist (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_blocklist_post(request):
    """Handle blocklist updates."""
    return web.HTTPFound(f"/federation/{request.match_info['fed_id']}/blocklist")


@require_auth()
async def handle_audit(request):
    """Audit log page."""
    html = "<h1>Audit Log (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_announcements(request):
    """Announcements page."""
    html = "<h1>Announcements (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_announcements_post(request):
    """Handle announcement creation."""
    return web.HTTPFound(f"/federation/{request.match_info['fed_id']}/announcements")


@require_auth()
async def handle_applications(request):
    """Applications page."""
    html = "<h1>Applications (TODO)</h1>"
    return web.Response(text=html, content_type='text/html')


@require_auth()
async def handle_applications_post(request):
    """Handle application approval/denial."""
    return web.HTTPFound(f"/federation/{request.match_info['fed_id']}/applications")
