# 🐧 Sir-Penguin — Modular Discord Bot + Web Dashboard

A modular Discord bot with an integrated web dashboard. Every feature is an
independent module (a "cog"), so you can add or change features without breaking
the rest. Everything is configured from the web dashboard, with Discord login —
no need to edit code or run commands to manage a server.

The bot and the dashboard run together in a **single process**.

- **21 modules**, **55 slash commands**, and a dashboard with a page per feature.
- Built primarily for the **Apollo2** Metin2 private server, but works on any server.

---

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [Running](#running)
- [Project structure](#project-structure)
- [Features](#features)
- [Dashboard](#dashboard)
- [Metin2 ticket bridge](#metin2-ticket-bridge)
- [Owner vs. per-server access](#owner-vs-per-server-access)
- [Deployment (Oracle Cloud)](#deployment-oracle-cloud)

---

## Requirements

- Python 3.10+
- A Discord application + bot token (with the **Server Members Intent** enabled)
- The dependencies in `requirements.txt`

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Setup

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your_bot_token
DISCORD_CLIENT_ID=your_application_id
DISCORD_CLIENT_SECRET=your_oauth_client_secret
DISCORD_REDIRECT_URI=http://YOUR_IP:5000/callback
FLASK_SECRET=any_long_random_string
```

Notes:

- **Server Members Intent** must be ON (Developer Portal → Bot → Privileged
  Gateway Intents). Invite tracking and several other features depend on it.
- `DISCORD_REDIRECT_URI` must exactly match the redirect URL registered in
  the Developer Portal (OAuth2 → Redirects).

---

## Running

```bash
python run.py
```

This starts the bot and the dashboard together. The dashboard is served on
port `5000` (e.g. `http://YOUR_IP:5000`).

Slash commands are synced **automatically** on startup — you don't run anything
manually.

---

## Project structure

```
run.py                 # entry point (starts bot + dashboard)
main.py                # bot core, cog loader, auto-sync, owner detection
cogs/                  # feature modules (one file per feature)
dashboard/
  app.py               # Flask dashboard (routes, OAuth login, per-page config)
  templates/           # one HTML template per page
utils/
  storage.py           # in-memory store, persisted to data/store.json
  perms.py             # bot-access permission helper
metin2_api/            # files you deploy on YOUR site (not the bot):
  schema.sql           #   database tables (MySQL/MariaDB)
  api.php              #   ready-to-use API the bot talks to
METIN2_TICKET_API.md   # spec for the Metin2 ticket bridge API
.github/workflows/     # auto-deploy on push (optional)
```

Data is kept in `utils/storage.py` (in memory) and persisted to
`data/store.json`. Reads return deep copies, so accidental mutation can't
corrupt the store.

---

## Features

Each feature is a cog under `cogs/` and has a matching dashboard page.

- **Welcome / Goodbye** — join/leave messages with a configurable channel.
- **Invites** — invite tracking with a contest mode and a leaderboard.
  Counts are reconciled against who is actually on the server, so leavers,
  fake/new accounts, rejoins, and self-invites don't inflate the totals.
  Diagnostic commands: `/recalcinvite`, `/inviteaudit`.
- **Colors** — self-assign color roles from a button panel (open to everyone
  by default; can be restricted).
- **Kingdoms** ("Alege regatul") — faction role picker with customizable
  buttons (name + emoji + color + role). One kingdom at a time; picking another
  swaps; clicking the held one leaves. Supports standard and custom/animated
  emojis. Open to everyone.
- **Tickets** — Discord ticket system with configurable types (support roles,
  category, open message, claim/close buttons, permission sync).
- **Giveaways** — start/end/reroll giveaways.
- **Notifications** — YouTube + TikTok upload/live alerts with per-creator
  keyword filters.
- **New accounts** — flag or list members whose account is newer than a
  threshold.
- **Cleanup** — bulk-delete a member's messages, plus an auto-delete rule;
  robust against errors so the bot never crashes mid-run.
- **Mass role / Mass DM** — apply roles or send DMs in bulk, in the background.
- **Rank-up** — apply rank roles across the server.
- **Embeds** — build, preview, save, and post embeds.
- **Avatar** — show user/server avatars and banners.
- **Games** — a number game and rock-paper-scissors.
- **Backup** — snapshot the server structure.
- **Metin2 ticket bridge** — optional; see below.

The full, always-current command list is on the dashboard page **"All commands"**
(with search), so it never goes stale.

---

## Dashboard

Log in with Discord. You only see and manage servers where you are an
administrator. Each feature has its own page; changes are saved to storage and
picked up by the bot's background loops.

Useful pages beyond the feature configs:

- **All commands** — every command grouped by category, with live search.
- **Test / deploy** — shows the running git commit; checks GitHub for updates;
  has a **Restart** button (owner-only).
- **Servers** — list of servers the bot is in (owner-only).

---

## Metin2 ticket bridge

An **optional** module (`cogs/metin2bridge.py`) that links tickets from your
Metin2 site/game with Discord. It does **not** touch the normal Discord ticket
system — it's separate, and toggled on/off from the dashboard.

How it works:

- The bot polls **your** API every few seconds for new tickets and new player
  messages, and creates/updates Discord channels for them.
- Each ticket channel has buttons — **✅ Resolved** (also deletes the channel
  after 10s), **🛠️ In progress**, **🙋 Claim**.
- Anything staff types in the channel is sent back to your API (shown in-game).
- Per-category routing: each in-game category can map to its own Discord
  category and its own staff roles; anything unmapped uses the defaults.

You provide the API. Two ready-made files are in `metin2_api/`:

- `schema.sql` — the three tables (`ticket`, `ticket_message`,
  `ticket_category`). Works on MySQL and MariaDB unchanged.
- `api.php` — a complete, ready-to-use implementation of the endpoints. Fill in
  your DB credentials and a secret token at the top, upload it to your site.

Then, on the dashboard's **Metin2 tickets** page, set the API base URL + token,
load the categories, add routes, and enable the bridge.

The full endpoint spec is in `METIN2_TICKET_API.md`.

---

## Owner vs. per-server access

- The **bot owner** (the Discord application owner, detected automatically on
  startup) has full access — including the restart button and the servers list.
- **Everyone else** who logs in can only manage servers where they are an
  admin. They cannot restart the bot, see other servers, or touch anything
  outside their own server. Owner-only actions fail safe: if the owner isn't
  known, access is denied rather than granted.

This makes it safe to add the bot to a friend's server and let them manage only
their own server.

---

## Deployment (Oracle Cloud)

The bot is designed to run on an always-on host (e.g. an Oracle Cloud free-tier
Ubuntu VM) under `systemd`.

- Run it as a service with `Restart=always` so the dashboard's restart button
  (which exits the process) brings it back up with the latest code.
- Optional auto-deploy: the workflow in `.github/workflows/` pulls and restarts
  the service on push to `main` (`deploy.sh`).

Typical service commands:

```bash
sudo systemctl restart sir-penguin
sudo systemctl status sir-penguin
```
