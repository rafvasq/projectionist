# Contributing to Projectionist

## Development setup

```bash
git clone https://github.com/rafvasq/projectionist.git
cd projectionist
uv venv && uv pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Activate the virtual environment:

- **Mac/Linux**: `source .venv/bin/activate`
- **Windows**: `.venv\Scripts\activate`

Then run any job module:

```bash
python -m jobs.curator --help
python -m jobs.cleaner --help
python -m jobs.digest --help
python -m bot.main --help
```

To build and run with Docker locally, use Docker Compose:

```bash
docker compose up -d
```

## Running tests

```bash
python -m pytest tests/ -v
```

Tests run automatically on every PR and push to `main`.

---

## Project structure

```text
projectionist/
├── core/                   # Foundational infrastructure
│   ├── config.py           # Configuration loading & validation
│   ├── storage.py          # Thread-safe atomic JSON read/write
│   ├── notifier.py         # Centralized WhatsApp notifier (Twilio)
│   ├── retention.py        # Retention policy evaluation
│   ├── plex.py             # Plex client & collection management
│   └── ai.py               # Generative AI curation & intent parsing
├── services/               # External API adapters
│   ├── overseerr.py        # Overseerr media search & request
│   ├── radarr.py           # Radarr movie lookup & deletion
│   ├── letterboxd.py       # Letterboxd list scraper & prompt builder
│   └── tracker.py          # Tracked collections synchronizer
├── bot/                    # Interactive WhatsApp bot
│   ├── app.py              # Flask app factory
│   ├── handlers.py         # Webhook endpoints & state transitions
│   ├── security.py         # Twilio webhook security & authorization
│   └── main.py             # Bot runner entrypoint (`python -m bot.main`)
├── jobs/                   # Scheduled cron jobs
│   ├── cleaner.py          # Automated cleaner (`python -m jobs.cleaner`)
│   ├── curator.py          # AI collection curator (`python -m jobs.curator`)
│   └── digest.py           # Media digest (`python -m jobs.digest`)
├── tests/                  # Test suite
│   ├── conftest.py         # Shared fixtures (make_movie, network guards)
│   ├── unit/               # Core & service unit tests
│   ├── jobs/               # Cleaner, curator, digest tests
│   └── bot/                # Bot webhook & handler tests
├── config.yaml             # Local configuration (gitignored)
├── config.example.yaml     # Commit-safe configuration template
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh           # Supercronic job scheduler
└── pyproject.toml
```

---

## Developing with Core Modules

When working with movie metadata or interacting with Plex:

```python
from core.plex import MovieMeta, PlexClient
from core.config import load_config

cfg = load_config()
client = PlexClient.from_config(cfg)
movies = client.fetch_movies()
```

