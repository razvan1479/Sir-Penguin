# Sir Penguin — Full Project Documentation

A modular Discord bot with a web dashboard, built with `discord.py` and Flask.
The bot and the dashboard run together in a single process. Everything is
configured from the dashboard; the bot reads that configuration and acts on it.

- **Modules (cogs):** 18
- **Slash commands:** 49
- **Dashboard pages:** 26 routes
- **Hosting:** Oracle Cloud Free Tier (Ubuntu, ARM/Always-Free)
- **Auto-deploy:** GitHub Actions on push to `main`

---

## 1. Architecture Overview

```
run.py                → entry point (starts bot + dashboard together)
main.py               → bot setup, loads all cogs, global error handler
cogs/                 → one file per feature (18 modules)
dashboard/
  app.py              → Flask app: routes, OAuth login, all config pages
  discord_api.py      → helpers for talking to the Discord API
  templates/          → one HTML page per feature (premium dark theme)
utils/
  storage.py          → in-memory data store with disk persistence
  perms.py            → permission checks (bot_access / has_bot_access)
data/                 → runtime data (store.json), git-ignored
.env                  → secrets (token, client id/secret) — git-ignored
.github/workflows/    → deploy.yml (auto-deploy on push)
deploy.sh             → pulls code + restarts service on the server
```

### How configuration flows
1. You log in to the dashboard with Discord (OAuth).
2. You change a setting on a page and save it.
3. The dashboard writes it to the shared store (`storage`) under a key such as
   `colors`, `contest`, `newacc`, `tickets`, etc.
4. The matching cog reads that key and behaves accordingly — usually within
   seconds, or on its next background cycle.

### Data storage
`utils/storage.py` is an in-memory cache keyed by guild ID, persisted to
`data/store.json`. Reads return a deep copy, so callers can't accidentally
mutate the cache. Each feature uses its own key (e.g. `guild → "colors"`).

---

## 2. Setup & Installation

### Requirements
```
discord.py>=2.4.0
python-dotenv>=1.0.0
Flask>=3.0.0
feedparser>=6.0.0
requests>=2.31.0
aiohttp
```
Install: `pip install -r requirements.txt`

### Environment variables (`.env`)
```
DISCORD_TOKEN=<bot token>
DISCORD_CLIENT_ID=<application client id>
DISCORD_CLIENT_SECRET=<oauth client secret>
DISCORD_REDIRECT_URI=http://<server-ip>:5000/callback
FLASK_SECRET=<any random string>
TWITCH_CLIENT_ID=        (optional / currently unused)
TWITCH_CLIENT_SECRET=    (optional / currently unused)
```

> **Important:** `DISCORD_REDIRECT_URI` must match **exactly** what is listed
> under **OAuth2 → Redirects** in the Discord Developer Portal, otherwise login
> fails silently.

### Running locally
```
python run.py
```
The dashboard is served on port `5000`. The bot connects using `DISCORD_TOKEN`.

### Required bot permissions
For everything to work, the bot needs **Administrator** (or at minimum:
Manage Server, Manage Roles, Manage Channels, Kick/Ban, Send Messages,
Embed Links, Read Message History, Mention Everyone). The bot's role must sit
**high** in the role list — it can only assign roles **below** its own top role.

---

## 3. Hosting & Deployment

- **Server:** Oracle Cloud Free Tier, Ubuntu, Always-Free ARM instance.
- **Service:** systemd unit `sir-penguin`
  (`WorkingDirectory=/home/ubuntu/Sir-Penguin`, `ExecStart=venv/bin/python run.py`).
- **Common commands:**
  ```
  sudo systemctl status sir-penguin     # is it running?
  sudo systemctl restart sir-penguin    # restart after changes
  ```
- **Dashboard URL:** `http://<server-ip>:5000`

### Auto-deploy
`.github/workflows/deploy.yml` runs on every push to `main`: it SSHes into the
server and runs `deploy.sh`, which pulls the latest code, installs dependencies
**only if `requirements.txt` changed**, and restarts the service.
GitHub secrets used: `SERVER_IP`, `SERVER_USER`, `SSH_PRIVATE_KEY`.

### Connecting to the server (SSH)
```
ssh -i "<path-to-key>" ubuntu@<server-ip>
```
On Windows, if you get *"Bad permissions / UNPROTECTED PRIVATE KEY FILE"*, lock
the key file down so only your user can read it (via `icacls`, removing
`Authenticated Users` and `Users`).

---

## 4. Modules (Cogs)

Each module is a self-contained feature. Below: what it does, its commands, and
its dashboard page.

### 4.1 Welcome
Greets new members. Configurable message, embed or plain text, optional banner
and avatar. When the Invites module is active, the welcome message also shows
who invited the new member (as a clickable profile link, so it still works even
if that inviter has left).
- Commands: `/welcome channel`, `/welcome test`
- Page: `/guild`

### 4.2 Goodbye
Announces when a member leaves. The member's name is rendered as a clickable
profile link (never a raw mention that would show as "unknown user").
Placeholders: `{user}`, `{username}`, `{inviter}`, `{server}`, `{count}`.
- Page: `/goodbye`

### 4.3 Invites (invite tracking + contest)
Tracks which invite each member used, keeps per-user invite counts, and powers
the invite **contest**. Invite totals are clamped so they can never go negative;
fake-account joins are handled correctly.

**Reliability features:**
- Joins are processed **one at a time per guild** (an async lock), so a burst of
  simultaneous joins (common during a contest) doesn't corrupt the detection.
- The invite cache is refreshed on startup, on invite create/delete, **and**
  every 5 minutes (a "gentle" resync that adds new invites and drops deleted
  ones without disturbing in-progress detection).

**Contest features:**
- Start/stop/schedule a contest; a live leaderboard can be posted to a channel
  and **edited in place** on an interval (no spam).
- Configurable number of entries shown (1–25).
- **Exclusions:** hide specific users (e.g. the server owner) from the
  leaderboard without touching their real invite counts.

- Commands: `/invites`, `/inviter`, `/invitedlist`, `/invitecodes`,
  `/addinvites`, `/removeinvites`, `/resetinvites`, `/leaderboard`,
  `/concurs start`, `/concurs stop`, `/concurs status`, `/concurs clasament`
- Pages: `/leaderboard`, `/invitelog`, `/contest`

> **Why "unknown/vanity" can appear:** the bot infers the invite by comparing
> use-counts before/after each join. It shows *vanity* when someone joined via
> the server's custom link, and *unknown* when it can't match any invite
> (e.g. the invite was used while the bot was offline, or the invite was
> deleted). No Discord bot can achieve 100% here — this is a platform limit.

### 4.4 Colors (name colors)
`/culori panou` posts a persistent panel of color buttons. Members click a color
to get a matching role. The bot **creates the color role on first use and reuses
it afterwards** (never duplicates), keeps a member to a single color, and lets
them toggle it off. Newly created color roles are moved **just under the bot's
top role** (via `edit_role_positions`) so the color actually shows on the name.
21 colors (all circle emojis, including a visible black `#010101`, since Discord
treats pure `#000000` as "no color"). Can be disabled from the dashboard.
- Command: `/culori panou`
- Page: `/culori`

### 4.5 New Accounts
Detects members whose Discord account was created recently (age computed from
the user ID). On join, it can auto-assign a role you choose and/or announce the
new account in a channel. A dashboard **"Scan whole server"** button applies the
role to all existing members under the threshold. Threshold, role and channel
are all set in the dashboard.
- Commands: `/conturinoi lista`, `/conturinoi verifica`
- Page: `/conturinoi`

### 4.6 Giveaway
`/giveaway` opens a single **modal form** (title, prize, winners, duration in
minutes, channel — blank channel = current channel). Ping is on by default.
Submitting posts the giveaway (embed + "Participate" button); the winner(s) are
drawn automatically at the end. When a giveaway ends, its message is edited to
"Giveaway ended" so the relative countdown no longer looks active.
- Commands: `/giveaway`, `/giveaway_start`, `/giveaway_end`, `/giveaway_reroll`
- Page: `/giveaway`

> Performance: many giveaways don't burden the bot — the ticker runs every 30s
> and ended giveaways are capped at 20 per server (older ones auto-pruned).

### 4.7 Tickets
A full ticket system. You define **ticket types** (name, emoji, button color,
support roles, category, open message, and which buttons appear: Close /
Close-with-reason / Claim). A `/ticket_panel` posts the panel members use to
open tickets.
- **Editing:** ticket types can be edited after creation (add/remove support
  roles, change options).
- **Permission sync:** when you add a support role to a type, the bot re-applies
  permissions to **already-open** tickets of that type (within ~10s), so the new
  role can see existing tickets too.
- **Close log:** on close, a clean embed is sent to the log channel with the
  ticket number, who opened/closed/claimed it, and the reason (or "closed
  without reason"). *No HTML transcript file is attached* — the log stays clean.
- Command: `/ticket_panel`
- Page: `/tickets`

### 4.8 Notifications (YouTube + TikTok)
Announces when a followed creator posts new content or goes live. **Only YouTube
and TikTok** are supported. Each creator can have **keywords** — if set, the bot
only announces when the video description/title contains one of them (blank =
announce everything). Checks run every 5 minutes (not instant).
- **YouTube** — reliable, uses the official RSS feed; announces new videos.
- **TikTok** — experimental. Uses a multi-method fallback:
  1. `tikwm.com` API (primary — description + thumbnail),
  2. TikTok page JSON (fallback — also detects live),
  3. regex (last resort).
  TikTok has no public API, so live detection in particular can miss.
- Live is only announced on the **offline → live transition** (not for a stream
  that was already live when you added the creator).
- Commands: `/notify list`, `/notify test`
- Page: `/notifications`

### 4.9 Rank-up roles
Automatic roles based on criteria (e.g. invites). Run/inspect from commands or
the dashboard.
- Commands: `/rankup run`, `/rankup status`
- Page: `/rankup`

### 4.10 Mass Role
Bulk add/remove a role to/from members. Jobs submitted from the dashboard are
executed by the bot within ~10s.
- Commands: `/massrole give_all`, `/massrole give_to`, `/massrole remove_all`,
  `/massrole remove_from`
- Page: `/massrole`

### 4.11 Mass DM
Send a direct message to many members at once (rate-limited to avoid spam
flags). Submitted as a job from the dashboard.
- Commands: `/dm_masa`, `/dm_stop`
- Page: `/massdm`

### 4.12 Embeds
Build, preview, send, list and delete custom embeds.
- Commands: `/embed send`, `/embed preview`, `/embed list`, `/embed delete`
- Page: `/embeds`

### 4.13 Backup
Snapshot/restore server structure (roles, categories, channels).
- Command: `/backup`
- Page: `/backups`

### 4.14 Games
- **Number game** (`/randome`) and **Rock-Paper-Scissors** (`/rps`).
- Also `/add`, `/remove`, `/alege` helpers.
- Pages: `/game`, `/rps`

### 4.15 Avatar / Banner
Show user/server avatars and banners.
- Commands: `/avatar`, `/banner`, `/serveravatar`, `/serverbanner`

### 4.16 Admin & utility
- `/leaveserver`, `/serverlist`, `/findlink`, `/randome`, `/alege`.
- Owner-only server management (bot removal) via `/servers` page.

### 4.17 Dashboard Sync
Keeps the dashboard's copy of the server's roles/channels/categories up to date
so the config pages always show current selectors. Runs in the background.

---

## 5. Dashboard

### Login
Discord OAuth. Click "Connect with Discord" → authorize → you land on the server
picker. Only servers where you have access are shown.

> The login button and server/module cards are fully clickable across their
> whole surface (the glowing border overlay does not intercept taps/clicks).

### Pages (routes)
| Route | Purpose |
|-------|---------|
| `/` | Landing / server picker |
| `/home/<id>` | Server overview (stats, module grid) |
| `/guild/<id>` | Welcome settings |
| `/goodbye/<id>` | Goodbye settings |
| `/culori/<id>` | Name colors on/off |
| `/conturinoi/<id>` | New-accounts detection + scan |
| `/leaderboard/<id>` | Invite leaderboard |
| `/invitelog/<id>` | Invite journal (view-only) |
| `/contest/<id>` | Invite contest + live board + exclusions |
| `/embeds/<id>` | Embed builder |
| `/giveaway/<id>` | Giveaway config / mode switch |
| `/notifications/<id>` | YouTube/TikTok notifications |
| `/rankup/<id>` | Auto rank-up roles |
| `/game/<id>`, `/rps/<id>` | Game settings |
| `/permissions/<id>` | Who can use the bot's admin commands |
| `/massrole/<id>` | Bulk role jobs |
| `/massdm/<id>` | Mass DM jobs |
| `/tickets/<id>` | Ticket types (create/edit/delete) |
| `/backups/<id>` | Server backups |
| `/appearance/<id>` | Dashboard theme customizer |
| `/test/<id>` | Version & update check |
| `/servers` | Owner-only bot management |

### Appearance / theme
`/appearance` lets you customize the dashboard's look (accent colors,
background, text, glass blur, animations) with live preview and presets. The
theme is stored and applied globally across all pages.

### Security
Every guild-scoped page verifies you actually have access to that guild —
requests for a guild you don't belong to return **403**.

---

## 6. Background Loops (scheduled work)

| Loop | Interval | What it does |
|------|----------|--------------|
| Invites contest | 30s | Starts/ends scheduled contests, posts/edits live board |
| Invites resync | 5 min | Refreshes invite cache (catches missed invites) |
| Giveaway ticker | 30s | Ends giveaways on time, safety net after restarts |
| New-accounts scan | 10s | Runs "scan whole server" jobs from the dashboard |
| Tickets perm-sync | 10s | Re-applies support-role permissions to open tickets |
| Notifications poller | 5 min | Checks YouTube/TikTok for new content/live |
| Mass role / Mass DM | ~10–12s | Executes bulk jobs submitted from the dashboard |
| Admin sync / leave | periodic | Housekeeping |

---

## 7. Known Limitations (honest notes)

- **Invite attribution is not 100%.** "Unknown"/"vanity" appears when Discord
  doesn't expose which invite was used (bot offline at join time, deleted
  invite, API lag, or a genuine vanity-link join). Mitigations are in place, but
  no bot can guarantee 100%.
- **TikTok is best-effort.** It has no public API; the multi-method fallback
  makes it much more stable, but it can still break if TikTok changes their
  site or blocks automated access. YouTube is solid.
- **HWID / IP alt-detection is impossible.** A Discord bot cannot see a user's
  hardware ID or IP — only Discord itself has that data. Alt detection can only
  be approximated via indirect signals (new accounts, burst joins, etc.).
- **Notifications are not instant** — up to the poll interval (5 min) of delay.
- **Color visibility on names** depends on role position: a member's name shows
  the color of their **highest colored role**. If they have a higher colored
  role, the name-color role won't show.

---

## 8. Common Operations Cheat-Sheet

- **Deploy new code:** `git push` to `main` → auto-deploy runs → service
  restarts. (Then Discord may take a few minutes to re-sync slash commands.)
- **Restart the bot:** `sudo systemctl restart sir-penguin`
- **Edit secrets:** `nano /home/ubuntu/Sir-Penguin/.env` → save (Ctrl+O, Enter,
  Ctrl+X) → restart the service.
- **Rotate a leaked token/secret:** reset it in the Discord Developer Portal,
  update `.env`, restart. (Always rotate anything that gets pasted somewhere.)
- **Log in when the button seems dead:** open `http://<server-ip>:5000/login`
  directly in the address bar.

---

*This document reflects the current state of the project: 18 modules, 49 slash
commands, 26 dashboard routes.*
