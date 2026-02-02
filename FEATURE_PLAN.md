# Discord Bot Feature Plan

Technical Note: Use web rendering to JPG for visual outputs (rate cards, invoices, palettes, contracts, etc.)

---

## Core Features

- [ ] **Federated Trust Network** - Trust scoring based on children count, upflow status, vouches, link age, approval rate. Trust factors: children_count (15%), upflow_status (20%), vouches (25%), link_age (15%), approval_rate (25%). Tiers: 0-20 untrusted, 21-50 neutral, 51-80 trusted, 81-100 highly trusted. Negative events decay at 2x rate of positive. Actions gated by tier: cross-server sync requires 50+, vouch others requires 60+, mediate disputes requires 80+
- [ ] **Bot Statistics** - Uptime, commands, messages scanned, guilds, federation stats
- [ ] **Server Invite Protection** - Admin-approved allowlist with approval workflow
- [ ] **Enhanced Portfolio System** - Gallery entries with categories, tags, featured piece
- [ ] **Commission Management** - Queue slots, status progression, TOS, price lists. Default stages: Inquiry → Accepted → Queued → In Progress → WIP Shared → Revision → Final Delivered → Completed → Archived. Transitions can be manual or triggered by client confirmation. Each stage change logs timestamp and notifies client via DM or channel ping based on preference

---

## Moderation & Safety

- [ ] **Auto-Escalation System** - Warning thresholds trigger automatic mute/ban escalation. Default thresholds configurable per guild. Suggested defaults: 3 active warnings = 1h mute, 5 = 24h mute, 7 = temp ban (7d), 10 = permanent ban. Category-specific paths supported. Cooldown between escalations: 24h. Each tier triggers DM notification with appeal info if configured
- [ ] **Shadow Mod Log** - Private channel logging all mod actions with rich embeds. Logs: warns, mutes, kicks, bans, unmutes, unbans, note additions, role changes (mod-relevant), message deletes (by mods), channel locks. Embed includes: action, target, moderator, reason, timestamp, case number. Case numbers sequential per guild
- [ ] **User History Lookup** - Combined timeline of warnings, notes, bans, mutes, scan matches
- [ ] **Probation System** - Restricted role for new/returning users until threshold met. Triggers: new account (<7d), rejoin after kick/ban, federation flag, manual assignment. Restrictions: no DMs to non-friends, no embeds/attachments, slowmode applied, limited channel access. Exit: X days clean (default 7), mod approval, or trust score threshold reached
- [ ] **Mod Action Templates** - Pre-configured reasons with shortcuts
- [ ] **Warning Expiry** - Default 30 days, configurable per guild. Warnings track: issued_at, expires_at, issued_by, reason, category. Expired warnings remain in history but don't count toward escalation. Permanent warnings supported. Expiry notifications optional
- [ ] **Report System** - Users report messages via context menu, creates private mod thread. Categories: harassment, scam_attempt, spam, nsfw_violation, impersonation, other. Priority: urgent (mod ping), normal (queue), low (batch review). Auto-close after 14 days inactive. Reporter receives outcome notification (resolved/dismissed) without details. Repeat reporters flagged for review
- [ ] **Action Reversal** - Quick undo for recent mod actions within grace period

---

## Art & Community

- [ ] **Color Palette Generator** - Color theory-based JPG output with multiple methods (complementary, analogous, triadic, split-complementary, tetradic, monochromatic) and configurable count
- [ ] **Commission Waitlist** - Join waitlist when slots full, position tracking, notification option (DM or channel ping)
- [ ] **Search User Art** - Channel-restricted image search, filters GIFs, paginated message links
- [ ] **Commission Review System** - Reviews with dispute workflow (upheld/removed/amended resolutions)
- [ ] **Art Prompt Roulette** - Random prompt generator with categories and difficulty
- [ ] **Commission Status Widget** - Auto-updating embed showing open/closed, slots, waitlist
- [ ] **Rate Card Generator** - Multiple template styles (minimal, detailed, colorful, professional), JPG output
- [ ] **Art Dice** - Roll random constraints for creative challenges

---

## Commission Enhancements

- [ ] **Commission Progress Tracker** - Artists update stages, clients notified at each stage
- [ ] **Commission Invoice Generator** - JPG invoice with payment tracking (paid/unpaid)
- [ ] **Revision Tracker** - Track revision requests per commission, warn when limit exceeded
- [ ] **Deadline Reminders** - Escalating reminders as deadline approaches
- [ ] **Scammer Database** - Track reported scammers with evidence, searchable before accepting commissions
- [ ] **Payment Confirmation** - Client confirms sent, artist confirms received, logged for disputes
- [ ] **Commission Contract Generator** - Formal agreement JPG with terms, limits, deadlines, payment schedule
- [ ] **Chargeback Alert** - Warn artists if user flagged for chargebacks in federation
- [ ] **Repeat Client Tracker** - Track returning clients, show commission count for relationship building
- [ ] **Commission Blacklist** - Personal blacklist, warns if blacklisted user tries to commission
- [ ] **Commission Summary** - Monthly/yearly summary: completed, revenue, turnaround, top clients
- [ ] **Commission Stages Customization** - Artists define their own workflow stages
- [ ] **Commission Tags** - Tag commissions (priority, complex, simple, rush) for personal organization

---

## Profile & Identity

- [ ] **Featured Commission** - Artists feature one completed piece on profile
- [ ] **Contact Preferences** - Preferred contact method (DM open, DM closed, email only) shown on profile
- [ ] **Timezone Display** - Profile shows user's current local time
- [ ] **Identity Verification** - Manual federation-wide verification with flexible mod discretion on evidence; propagates to all federated servers
- [ ] **Vouching System** - Users vouch for each other after completed transaction proof, visible on profile. Requires transaction proof (screenshot, payment confirmation, or mod-verified). Vouch cooldown: 1 vouch per pair per 30 days. Vouches display: count, recent vouchers (last 5), oldest vouch date. Mutual vouches weighted higher. Vouch removal requires mod review

---

## Utility

- [ ] **Bookmark System** - Instant or delayed DM delivery with message link + note
- [ ] **AFK System** - Mention collection, paginated "Show More" button on return
- [ ] **Personal Notes** - Private user notes only they can retrieve
- [ ] **Command Aliases** - Admin-defined shortcuts for common commands
- [ ] **Profile Quick Edit** - Single command to update multiple profile fields
- [ ] **Commission Quick Add** - Single command to add commission with all details
- [ ] **Batch Portfolio Upload** - Add multiple portfolio entries at once
- [ ] **Export Data** - Users export their data as JSON for backup

---

## Communication

- [ ] **Anonymous Feedback Box** - Anonymous feedback to mods in private channel
- [ ] **Commission Opening Announcements** - Auto-post when artist opens commissions with rate card
- [ ] **Important Message Acknowledgment** - `tagimportant @user <message_id>` pings every 6h until user reacts with checkmark

---

## Federation/Network

- [ ] **Cross-Server Reputation** - +/- ratings, 2 per 12h limit, aggregate across federation
- [ ] **Network-Wide Artist Directory** - Opt-in searchable directory across federated servers
- [ ] **Federated Blocklist** - Aggregate flagged users across federation
- [ ] **Network Statistics Dashboard** - Aggregate stats across federation
- [ ] **Parent Server Announcements** - Push announcements to children (opt-in)
- [ ] **Network Health Monitor** - Track sync latency, failed syncs, connection status with alerts
- [ ] **Shared Tag Database** - Common artist tags across federation for consistent searching
- [ ] **Federation Audit Log** - Queryable log of cross-server sync events
- [ ] **Trust Decay** - Inactive links gradually lose trust score
- [ ] **Cross-Server Commission Search** - Search artists by commission status, price range, style tags
- [ ] **Federated Commission Escrow** - Track/flag payment disputes across federation
- [ ] **Cross-Server Portfolio Sync** - Portfolio syncs across federated servers user is in
- [ ] **Network Ban Appeals** - Appeals reviewed by parent server mods
- [ ] **Federated Artist Verification** - Verification in parent server propagates to children
- [ ] **Server Reputation** - Servers have reputation based on member behavior, dispute resolution
- [ ] **Federation Invite System** - Servers apply to join federation, parent reviews application
- [ ] **Network Alerts** - Broadcast urgent alerts across federation (scammer active, raid, etc.)
- [ ] **Cross-Server User Notes** - Share mod notes across federation (opt-in)
- [ ] **Commission Matching** - Describe needs, bot suggests matching artists
- [ ] **Federation Tiers** - Different membership tiers (observer, member, trusted, core) with different sync permissions
- [ ] **Federation Voting** - Major decisions require vote from member servers
- [ ] **Shared Scammer Reports** - Reports shared across federation with evidence chain
- [ ] **Federation Metrics Dashboard** - Compare server stats to federation averages
- [ ] **Cross-Server Warnings** - Share warnings across federation for pattern detection
- [ ] **Federation Roles** - Define roles that sync across federation
- [ ] **Cross-Server Mute** - Mute user across all federated servers simultaneously
- [ ] **Federation Changelog** - Track changes to federation settings, membership, policies
- [ ] **Server Introductions** - Auto-post introduction when new server joins federation
- [ ] **Sync Preferences** - Granular control: what to sync, from which tiers, with optional delay

---

## Portfolio & Showcase

- [ ] **Portfolio Categories** - Organize into categories (illustrations, icons, reference sheets) with separate views
- [ ] **Portfolio Sorting** - Sort by date, category, or custom order
- [ ] **Portfolio Privacy** - Mark pieces as private or federation-only
- [ ] **Before/After Showcase** - Entry type showing progression (sketch to final)
- [ ] **Commission Examples** - Tag pieces as commission examples for specific types

---

## Safety & Trust

- [ ] **Transaction History** - View completed transactions between two users across federation
- [ ] **Trust Score Breakdown** - Detailed view of trust score components
- [ ] **Dispute Mediation Queue** - Trusted users help resolve disputes before mod escalation
- [ ] **Channel Access by Trust** - Certain channels only accessible above trust threshold or with federation verification
- [ ] **Permissions Audit** - Command to audit who has dangerous permissions for security review
- [ ] **Message Pattern Detection** - Detect repetitive messages, excessive caps, spam patterns (alerts only)

---

## Automation

- [ ] **Commission Auto-Close** - Close commissions when slots fill, reopen when freed
- [ ] **Waitlist Auto-Promote** - Auto-notify next user when slot opens, timeout before moving to next
- [ ] **Inactive Commission Cleanup** - Flag commissions with no updates in X days
- [ ] **Auto-Archive Completed** - Archive commission data after X days of completion
- [ ] **Vacation Mode** - Auto-closes commissions, shows return date, pauses deadline reminders

---

## Analytics

- [ ] **Commission Analytics** - Stats: average completion time, busiest months, most requested types
- [ ] **Profile Views** - Track profile view count, weekly/monthly stats
- [ ] **Portfolio Engagement** - Track which portfolio pieces get most views
- [ ] **Commission Conversion Rate** - Track inquiries vs actual commissions
- [ ] **Peak Activity Times** - Show when user/server is most active
- [ ] **Reputation Trends** - Graph reputation changes over time
- [ ] **Federation Health Score** - Composite score of federation activity, sync success, dispute resolution

---

## Quality of Life

- [ ] **Commission Search** - Artists search commission history by client, type, date, status
- [ ] **Duplicate Detection** - Warn if user tries to join waitlist twice or duplicate commission
- [ ] **Client Appreciation** - Track supportive clients (multiple commissions, pays on time), optional shoutouts

---

## Notifications & Preferences

- [ ] **Notification Preferences** - Users configure what they get DM'd about
- [ ] **Quiet Hours** - Users set hours when bot won't DM them, messages queued for later
- [ ] **Digest Mode** - Daily digest instead of individual DMs

---

## Data & Privacy

- [ ] **Data Retention Settings** - Configure how long data kept before auto-purge
- [ ] **Privacy Mode** - Hide profile from public searches
- [ ] **Incognito Commissions** - Private commissions that don't appear in public stats
- [ ] **GDPR Export** - Full data export in human-readable format

---

## Moderation Tools

- [ ] **Mod Action Reasons Audit** - Review if mod reasons are consistent, flag low-effort reasons
- [ ] **Escalation Paths** - Define custom escalation paths for different violation types

---

## Server Organization

- [ ] **Reaction Roles** - Assign roles via reactions, mutually exclusive groups, verification required option

---

## Custom Content

- [ ] **Custom Commands** - Admins create text/embed responses triggered by keywords
- [ ] **Auto-Responder Enhancements** - Regex support, cooldowns, role restrictions, random responses from list
- [ ] **Form Builder** - Create multi-question forms, responses logged to channel, used for applications/signups

---

## Automation & Workflows

- [ ] **Trigger Chains** - When event X happens, execute action Y (e.g., when user gets role A, also give role B)
- [ ] **Scheduled Actions** - Schedule role additions/removals, channel permission changes, message sends at future times

---

## Role Management

- [ ] **Temporary Roles** - Assign roles that auto-expire after duration
- [ ] **Role Request System** - Users request roles, mods approve/deny, request tracking
- [ ] **Role Bundles** - Assign multiple roles at once with one command/action

---

## Content Management

- [ ] **Cross-Post Manager** - Auto-share content across federation (e.g., art-share channels), configurable filters, source attribution

---

## Server Customization

- [ ] **Bot Persona** - Customize bot name, avatar, response style per server
- [ ] **Command Cooldowns** - Set per-command cooldowns globally or per-user/role

---

## Web UI System

Lightweight, minimal CSS/JS web interface (old internet aesthetic - fast, no frameworks, vanilla HTML/CSS/JS). Authentication via Discord OAuth.

### Server Admin Panel
- [ ] **Dashboard** - Server overview, quick stats, recent activity
- [ ] **Modules** - Enable/disable modules, per-module settings
- [ ] **Moderation Settings** - Warning thresholds, escalation paths, auto-mod config
- [ ] **Auto-Responders** - Create/edit/delete auto-responders, test patterns
- [ ] **Custom Commands** - Manage custom commands, view usage stats
- [ ] **Forms** - Build and manage forms, view submissions
- [ ] **Roles** - Reaction roles setup, role requests queue, temporary roles, bundles
- [ ] **Automation** - Trigger chains builder, scheduled actions calendar
- [ ] **Commission Settings** - Stages, templates, defaults (if commission module enabled)
- [ ] **Logs** - View mod logs, action history, filterable
- [ ] **Bot Persona** - Customize bot name/avatar/style for this server

### Federation Admin Panel
- [ ] **Dashboard** - Federation overview, member servers, health status
- [ ] **Member Servers** - View/manage linked servers, trust scores, sync status
- [ ] **Trust Network** - Visualize trust relationships, adjust weights
- [ ] **Sync Settings** - Configure what syncs, from which tiers, delays
- [ ] **Cross-Post Channels** - Manage art-share and cross-post configurations
- [ ] **Blocklist** - Federation-wide blocklist management
- [ ] **Audit Log** - Federation sync events, membership changes
- [ ] **Announcements** - Push announcements to children servers
- [ ] **Applications** - Review server applications to join federation

### Bot Owner Panel
- [ ] **Dashboard** - Global stats, all servers, uptime, resource usage
- [ ] **Servers** - List all servers, per-server controls, leave server
- [ ] **Federation Management** - All federations overview, intervene if needed
- [ ] **AI Checker Module**
  - [ ] **Pricing** - Set cost per use, bulk discounts, free tier limits
  - [ ] **Server Credits** - View/adjust server credits, transaction history
  - [ ] **Usage Stats** - Per-server usage, revenue tracking, cost analysis
  - [ ] **Enable/Disable** - Toggle AI checker availability per server
  - [ ] **Rate Limits** - Configure rate limits, abuse prevention
  - [ ] **Billing** - Payment integration settings, invoice generation
- [ ] **Global Settings** - Bot-wide defaults, feature flags
- [ ] **Maintenance** - Restart, update, backup controls
- [ ] **Logs** - Global error logs, performance metrics

---
