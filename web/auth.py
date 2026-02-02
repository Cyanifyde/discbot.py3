"""
Discord OAuth2 authentication for web UI.
"""
import os
import secrets
from aiohttp import web, ClientSession
from functools import wraps
import logging

logger = logging.getLogger(__name__)

# Discord OAuth2 endpoints
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH_URL = f"{DISCORD_API_BASE}/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"
DISCORD_USER_URL = f"{DISCORD_API_BASE}/users/@me"

# Session storage (in production, use Redis or similar)
sessions = {}


def setup_auth(app, bot):
    """
    Set up authentication routes and middleware.

    Args:
        app: aiohttp application
        bot: Discord bot instance
    """
    app['bot'] = bot
    app['sessions'] = sessions

    # OAuth2 configuration
    client_id = os.getenv('DISCORD_CLIENT_ID')
    client_secret = os.getenv('DISCORD_CLIENT_SECRET')
    redirect_uri = os.getenv('DISCORD_REDIRECT_URI', 'http://localhost:8080/auth/callback')

    if not client_id or not client_secret:
        logger.warning("Discord OAuth2 credentials not configured. Web auth will not work.")

    app['oauth_config'] = {
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri
    }

    # Set up routes
    app.router.add_get('/auth/login', handle_login)
    app.router.add_get('/auth/callback', handle_callback)
    app.router.add_post('/auth/logout', handle_logout)  # POST for CSRF protection

    # Middleware for session handling
    app.middlewares.append(session_middleware)


@web.middleware
async def session_middleware(request, handler):
    """Middleware to handle session management."""
    import time
    session_token = request.cookies.get('session_token')

    if session_token and session_token in request.app['sessions']:
        session = request.app['sessions'][session_token]
        # Check expiry
        expires_at = session.get('expires_at', 0)
        if expires_at and time.time() > expires_at:
            # Session expired, remove it
            del request.app['sessions'][session_token]
            request['user'] = None
        else:
            request['user'] = session
    else:
        request['user'] = None

    response = await handler(request)
    return response


def require_auth(permission_level='user'):
    """
    Decorator to require authentication for a route.

    Args:
        permission_level: Required permission level ('user', 'admin', 'owner')
    """
    def decorator(handler):
        @wraps(handler)
        async def wrapper(request):
            user = request.get('user')

            if not user:
                # Redirect to login
                return web.HTTPFound('/auth/login')

            # Check permission level
            if permission_level == 'admin':
                guild_id = request.match_info.get('guild_id')
                if not guild_id:
                    return web.Response(text='Forbidden: Guild ID required', status=403)
                # Always validate admin permission when admin level required
                if not await is_guild_admin(request, user, guild_id):
                    return web.Response(text='Forbidden: Admin access required', status=403)

            elif permission_level == 'owner':
                if not await is_bot_owner(request, user):
                    return web.Response(text='Forbidden: Bot owner access required', status=403)

            return await handler(request)
        return wrapper
    return decorator


async def handle_login(request):
    """Handle login redirect to Discord OAuth2."""
    oauth_config = request.app['oauth_config']

    if not oauth_config['client_id']:
        return web.Response(text='OAuth2 not configured', status=500)

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    request.app['sessions'][state] = {'pending': True}

    # Redirect to Discord OAuth2
    oauth_url = (
        f"{DISCORD_OAUTH_URL}?"
        f"client_id={oauth_config['client_id']}&"
        f"redirect_uri={oauth_config['redirect_uri']}&"
        f"response_type=code&"
        f"scope=identify guilds&"
        f"state={state}"
    )

    return web.HTTPFound(oauth_url)


async def handle_callback(request):
    """Handle OAuth2 callback from Discord."""
    code = request.query.get('code')
    state = request.query.get('state')

    if not code or not state:
        return web.Response(text='Invalid callback', status=400)

    # Verify state
    if state not in request.app['sessions']:
        return web.Response(text='Invalid state', status=400)

    oauth_config = request.app['oauth_config']

    # Exchange code for token
    async with ClientSession() as session:
        data = {
            'client_id': oauth_config['client_id'],
            'client_secret': oauth_config['client_secret'],
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': oauth_config['redirect_uri']
        }

        async with session.post(DISCORD_TOKEN_URL, data=data) as resp:
            if resp.status != 200:
                return web.Response(text='Failed to get token', status=500)

            token_data = await resp.json()
            access_token = token_data['access_token']

        # Get user info
        headers = {'Authorization': f"Bearer {access_token}"}
        async with session.get(DISCORD_USER_URL, headers=headers) as resp:
            if resp.status != 200:
                return web.Response(text='Failed to get user info', status=500)

            user_data = await resp.json()

        # Get user guilds
        async with session.get(f"{DISCORD_API_BASE}/users/@me/guilds", headers=headers) as resp:
            if resp.status != 200:
                guilds = []
            else:
                guilds = await resp.json()

    # Create session
    session_token = secrets.token_urlsafe(32)
    import time
    expires_at = time.time() + 86400  # 24 hours
    request.app['sessions'][session_token] = {
        'user_id': user_data['id'],
        'username': user_data['username'],
        'discriminator': user_data.get('discriminator', '0'),
        'avatar': user_data.get('avatar'),
        'guilds': guilds,
        'access_token': access_token,
        'expires_at': expires_at
    }

    # Clean up pending state
    del request.app['sessions'][state]

    # Set cookie and redirect
    response = web.HTTPFound('/')
    # Use secure=True only for HTTPS (check redirect_uri)
    is_https = oauth_config['redirect_uri'].startswith('https://')
    response.set_cookie(
        'session_token',
        session_token,
        max_age=86400,
        httponly=True,
        secure=is_https,
        samesite='Strict'
    )

    return response


async def handle_logout(request):
    """Handle logout."""
    session_token = request.cookies.get('session_token')

    if session_token and session_token in request.app['sessions']:
        del request.app['sessions'][session_token]

    response = web.HTTPFound('/')
    response.del_cookie('session_token')

    return response


async def is_guild_admin(request, user, guild_id):
    """
    Check if user is an admin in the specified guild.

    Args:
        request: aiohttp request
        user: User session data
        guild_id: Guild ID to check

    Returns:
        bool: True if user is admin
    """
    bot = request.app['bot']

    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return False

        member = guild.get_member(int(user['user_id']))
        # Fallback to fetch_member if not in cache
        if not member:
            try:
                member = await guild.fetch_member(int(user['user_id']))
            except Exception:
                return False
        
        if not member:
            return False

        return member.guild_permissions.administrator
    except Exception as e:
        logger.error(f"Error checking guild admin: {e}")
        return False


async def is_bot_owner(request, user):
    """
    Check if user is the bot owner.

    Args:
        request: aiohttp request
        user: User session data

    Returns:
        bool: True if user is bot owner
    """
    bot = request.app['bot']
    bot_owner_id = os.getenv('BOT_OWNER_ID')

    if not bot_owner_id:
        # Fallback to Discord app owner
        app_info = await bot.application_info()
        bot_owner_id = str(app_info.owner.id)

    return user['user_id'] == bot_owner_id
