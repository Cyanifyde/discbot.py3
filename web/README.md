# Discord Bot Web UI

A web-based administration interface for the Discord bot with an "old internet aesthetic" design.

## Features

### Server Admin Panel (`/admin`)
- **Dashboard**: Server overview with quick stats and recent activity
- **Modules**: Enable/disable bot modules and configure per-module settings
- **Moderation Settings**: Configure warning thresholds, escalation paths, and auto-mod
- **Auto-Responders**: Create, edit, and delete auto-responders with pattern testing
- **Custom Commands**: Manage custom commands and view usage statistics
- **Forms**: Build forms and view submissions
- **Roles**: Manage reaction roles, role requests, temporary roles, and role bundles
- **Automation**: Configure trigger chains and scheduled actions
- **Commission Settings**: Set up commission stages, templates, and defaults
- **Logs**: View moderation logs and action history with filtering
- **Bot Persona**: Customize bot name, avatar, and style per server

### Bot Owner Panel (`/owner`)
- **Dashboard**: Global statistics, all servers, and uptime metrics
- **Servers**: List all servers, per-server controls, and leave server functionality
- **AI Checker Module**:
  - Pricing configuration (cost per use, bulk discounts, free tier)
  - Server credits management (view/adjust, transaction history)
  - Usage statistics (per-server usage, revenue tracking)
  - Enable/disable per server
  - Rate limit configuration
- **Global Settings**: Default settings and feature flags
- **Maintenance**: Restart bot, create backups, database maintenance
- **Logs**: Global error logs and performance metrics

## Setup

### Requirements
- Python 3.8+
- aiohttp
- Discord bot token

### Environment Variables

```bash
# Discord OAuth2 (required for web auth)
DISCORD_CLIENT_ID=your_client_id
DISCORD_CLIENT_SECRET=your_client_secret
DISCORD_REDIRECT_URI=http://localhost:8080/auth/callback

# Bot Owner (optional, falls back to app owner)
BOT_OWNER_ID=your_discord_user_id
```

### Installation

1. Install dependencies:
```bash
pip install aiohttp
```

2. Set up Discord OAuth2 application:
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Select your application
   - Go to OAuth2 → General
   - Add redirect URL: `http://localhost:8080/auth/callback`
   - Copy Client ID and Client Secret

3. Configure environment variables (create a `.env` file or set them in your environment)

### Running the Web Server

From your bot code:

```python
from web.server import run_web_server

# Start web server alongside bot
web_server = await run_web_server(bot, host='127.0.0.1', port=8080)
```

The web UI will be available at `http://localhost:8080`

## Authentication

The web UI uses Discord OAuth2 for authentication. Users must:
1. Log in with their Discord account
2. Have appropriate permissions:
   - **Server Admin Panel**: Administrator permission in the server
   - **Bot Owner Panel**: Must be the bot owner (set via `BOT_OWNER_ID`)

## Design Philosophy

The web UI follows an "old internet aesthetic" with:
- Monospace fonts (Courier New)
- Bold borders and box shadows
- Gradient backgrounds
- No modern JavaScript frameworks
- Simple, straightforward navigation
- Maximum compatibility

## Routes

### Public Routes
- `GET /` - Landing page
- `GET /auth/login` - Redirect to Discord OAuth2
- `GET /auth/callback` - OAuth2 callback handler
- `GET /auth/logout` - Logout

### Admin Routes (requires server admin)
- `GET /admin` - Guild selection
- `GET /admin/{guild_id}/` - Server dashboard
- `GET/POST /admin/{guild_id}/modules` - Module management
- `GET/POST /admin/{guild_id}/moderation` - Moderation settings
- `GET/POST /admin/{guild_id}/autoresponders` - Auto-responders
- `GET/POST /admin/{guild_id}/commands` - Custom commands
- `GET/POST /admin/{guild_id}/forms` - Form builder
- `GET/POST /admin/{guild_id}/roles` - Role management
- `GET/POST /admin/{guild_id}/automation` - Automation
- `GET/POST /admin/{guild_id}/commissions` - Commission settings
- `GET /admin/{guild_id}/logs` - Logs viewer
- `GET/POST /admin/{guild_id}/persona` - Bot persona

### Owner Routes (requires bot owner)
- `GET /owner` - Owner dashboard
- `GET/POST /owner/servers` - Server management
- `GET/POST /owner/ai_checker` - AI Checker module
- `GET/POST /owner/settings` - Global settings
- `GET/POST /owner/maintenance` - Maintenance tools
- `GET /owner/logs` - Global logs

## Security

- Session tokens are stored server-side
- HTTP-only cookies prevent XSS attacks
- CSRF protection via OAuth2 state parameter
- Permission checks on every route
- No user input is executed directly

## Future Enhancements

- Template rendering with Jinja2
- HTTPS support
- Redis session storage for production
- WebSocket support for real-time updates
- Enhanced analytics visualizations
- Mobile-responsive design (while keeping the aesthetic)

## Troubleshooting

**OAuth2 not working:**
- Verify `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` are set
- Check that redirect URI matches in Discord Developer Portal
- Ensure the redirect URI uses the correct protocol (http/https)

**Permission denied:**
- Verify user has Administrator permission in the Discord server
- Check that `BOT_OWNER_ID` is set correctly for owner panel

**Server not starting:**
- Check that port 8080 is not already in use
- Verify all dependencies are installed
- Check logs for error messages

## Contributing

When adding new routes or features:
1. Add route handlers to appropriate files in `web/routes/`
2. Use `@require_auth()` decorator for authentication
3. Follow the existing HTML structure and CSS classes
4. Keep the old internet aesthetic consistent
5. Add documentation to this README

## License

Same as the main Discord bot project.
