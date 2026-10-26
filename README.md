# Projectionist

![Tests](https://github.com/rafvasq/projectionist/actions/workflows/test.yml/badge.svg)
![Release](https://img.shields.io/github/v/release/rafvasq/projectionist)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

Projectionist is a self-hosted AI concierge, curation layer, and automated storage manager for Plex.

---

## Features

- **WhatsApp Bot & Requests**: Request movies and TV shows conversationally using natural language. Paste Letterboxd list or movie links to batch request titles and optionally create Plex collections tagged "Keep Forever". Tracks download progress and notifies users when requested items are ready on Plex, and tells everyone when a new collection is created. Admins get slash commands (`/keep`, `/leaving`, ...); family members can be given a user role that only requests and receives notices.
- **Automated Clean Up & Leaving Soon**: Flags movies nobody has added or watched for a configured time, holds them in a queue for a grace period (30 days by default) while a "Leaving Soon" collection in Plex shows what is going, then removes them via Radarr, a limited number per run, and clears each removed movie's Overseerr requests and takes it off your Plex watchlist (through your Plex account token) so it cannot be re-requested. `/keep` rescues a film. Tagged titles are protected (everything brought in from a list is tagged Keep Forever), and summaries and failure alerts go to admins on WhatsApp. Configure the threshold with `max_age_days` or define your own `rules` (see `config.example.yaml`); the cleaner starts in dry-run.
- **AI Themed Collections**: Generates creative, themed collections from unwatched library titles and pins them to your Plex home screen. Cooldown tracking ensures films are not repeated across consecutive runs.
- **Weekly Digest**: Sends a scheduled WhatsApp digest showcasing newly added media alongside recommendations surfaced from your collection.

---

## Requirements

- Plex Media Server
- Gemini or Ollama
- Overseerr & Radarr
- Twilio
- Docker

---

## Setup

### 1. Download Files

```bash
curl -LO https://raw.githubusercontent.com/rafvasq/projectionist/main/docker-compose.yml
curl -LO https://raw.githubusercontent.com/rafvasq/projectionist/main/config.example.yaml
cp config.example.yaml config.yaml
```

### 2. Configure `config.yaml`

```yaml
plex:
  url: "http://localhost:32400"
  token: "<plex-token>"
  library: "Movies"

ai:
  provider: gemini
  model: "gemini-3.5-flash-lite"
  api_key: "<gemini-api-key>"

whatsapp:
  enabled: true
  allowed_numbers:
    - "whatsapp:<your-number>"
  twilio_account_sid: "<twilio-account-sid>"
  twilio_auth_token: "<twilio-auth-token>"
  twilio_phone_number: "whatsapp:<twilio-phone-number>"

overseerr:
  url: "http://localhost:5055"
  api_key: "<overseerr-api-key>"
  webhook_secret: "<long-random-string>"   # required, see Webhooks

radarr:
  url: "http://localhost:7878"
  api_key: "<radarr-api-key>"

deletion:
  enabled: true
  dry_run: true
  grace_days: 30
  max_deletions_per_run: 10
  max_age_days: 120      # flag a movie nobody added or watched for this long
  protect_tag: "Keep Forever"
  leaving_soon_row: true
  leaving_soon_limit: 25
```

### 3. Run

```bash
docker compose up -d
```

On start, the scheduler container runs the curator and cleaner once, then on a schedule (curator Mondays 03:00 by default, cleaner Thursdays 04:00, digest Fridays 12:00). The first cleaner run flags every movie that matches your rules, so keep `dry_run: true` until you have checked `/leaving` and the Leaving Soon row in Plex.

For local LLM inference via Ollama:

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3
```

---

## Webhooks

- **Twilio WhatsApp**: Point webhook URL to `https://<domain>/whatsapp` (HTTP POST). Requests are verified with Twilio's signature using `whatsapp.twilio_auth_token`, and anything unsigned is rejected with `403`. If the bot sits behind a proxy or tunnel that doesn't send `X-Forwarded-Proto`/`X-Forwarded-Host`, set `whatsapp.public_url` (for example `https://bot.example.com`) so the signature is checked against the URL Twilio actually calls. For local development only, `whatsapp.verify_signature: false` skips the check.
- **Overseerr**: Under Settings > Notifications > Webhook, point webhook URL to `https://<domain>/overseerr` and enable **Media Available**. Set a long random string as `overseerr.webhook_secret` (or `OVERSEERR_WEBHOOK_SECRET`) and put the same value in Overseerr's **Authorization Header** field. Requests without it are rejected with `403`.

---

## WhatsApp Commands

Messages starting with `/` are commands, and they are for admins. Everything else is treated as a movie or show to request, or a Letterboxd link.

Roles: by default every number in `allowed_numbers` is an admin. To let family members use the bot without admin power, list them under `whatsapp.users` with `role: user` (see `config.example.yaml`). Users can request films and shows, send Letterboxd film links, and receive new-arrival notices and the weekly digest. Slash commands and Letterboxd list imports are admin-only, and cleaner summaries and alerts go to admins only.

| Command | What it does |
| --- | --- |
| `/keep <title> [year]` | Tag a movie already in Plex with "Keep Forever" so the cleaner skips it. Matching is forgiving (typos, accents, a missing "the", words in any order). An exact title is tagged immediately; anything else asks "Did you mean…?" first. |
| `/unkeep <title> [year]` | Remove the tag, with the same matching and confirmation. |
| `/leaving` | List the movies flagged for deletion with the date each one goes and why, plus whether the cleaner is disabled, in dry-run, or active. |
| `/log` | Show the cleaner's last five runs: what it flagged, removed, or failed on, and whether each was a dry run. |
| `/recent [days]` | List movies added to the library in the last N days (default 7, up to 365). |
| `/downloads` | Show what Radarr is downloading, and what it is still waiting on a release for (searched, nothing found yet, or not released). |
| `/status` | Show collections from imported lists that are still waiting on downloads (for example `3/10 ready`). A collection is forgotten once everything has arrived. |
| `/drop <collection>` | Delete a Plex collection (matched by name; "Leaving Soon" is refused). It always asks first: **1** deletes the collection and the films stay protected, **2** also removes Keep Forever from its films. Films from an imported list that haven't downloaded yet stay requested in Overseerr but stop being tracked, so they won't rebuild the collection. |
| `/help` | List the commands. |

---

## CLI Usage

Jobs can be executed directly from the command line:

```bash
# Flag idle movies and delete those past their grace window
python -m jobs.cleaner [--dry-run | --no-dry-run]

# Generate AI collections and sync tracked collections
python -m jobs.curator

# Send weekly digest
python -m jobs.digest

# Run WhatsApp bot server
python -m bot.main
```

---

## Development

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python -m pytest tests/ -v
```

---

## License

MIT
