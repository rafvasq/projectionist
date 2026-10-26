# Projectionist

![Tests](https://github.com/rafvasq/projectionist/actions/workflows/test.yml/badge.svg)
![Release](https://img.shields.io/github/v/release/rafvasq/projectionist)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

Projectionist is a self-hosted AI concierge, curation layer, and automated storage manager for Plex.

---

## Features

- **WhatsApp Bot & Requests**: Request movies and TV shows conversationally using natural language. Paste Letterboxd list or movie links to batch request titles and optionally create Plex collections tagged "Keep Forever". Tracks download progress and notifies users when requested items are ready on Plex.
- **Automated Clean Up & Leaving Soon**: Automatically prunes unwatched movies via Radarr once they exceed a configured age threshold. Protects tagged titles, tracked collections, and recently watched media. Pins a dynamic "Leaving Soon" collection to Plex prior to deletion and dispatches deletion summaries and failure alerts to WhatsApp.
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

radarr:
  url: "http://localhost:7878"
  api_key: "<radarr-api-key>"

deletion:
  enabled: true
  dry_run: true
  max_age_days: 120
  protect_tag: "Keep Forever"
  leaving_soon_row: true
  leaving_soon_limit: 25
```

### 3. Run

```bash
docker compose up -d
```

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

Messages starting with `/` are commands. Everything else is treated as a movie or show to request, or a Letterboxd link.

| Command | What it does |
| --- | --- |
| `/keep <title> [year]` | Tag a movie already in Plex with "Keep Forever" so the cleaner skips it. Matching is forgiving (typos, accents, a missing "the", words in any order). An exact title is tagged immediately; anything else asks "Did you mean…?" first. |
| `/unkeep <title> [year]` | Remove the tag, with the same matching and confirmation. Movies in a protected tracked collection stay protected, and the reply tells you so. |
| `/leaving` | List the movies the cleaner would delete right now, and whether it is disabled, in dry-run, or active. |
| `/status` | Show download progress for tracked collections (for example `3/10 ready`). |
| `/help` | List the commands. |

---

## CLI Usage

Jobs can be executed directly from the command line:

```bash
# Clean up expired media
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
