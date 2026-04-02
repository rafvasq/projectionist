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

Then run:

```bash
python curator.py
```

To build and run with Docker locally, swap `docker-compose.yml` to use `build: .` instead of the pre-built image.

## Running tests

```bash
python -m pytest tests/ -v
```

Tests run automatically on every PR and push to `main`.

---

## Project structure

```text
projectionist/
├── curator.py              # entry point
├── plex_client.py          # Plex connection, metadata, collection management
├── config.yaml             # local config (gitignored)
├── config.example.yaml     # commit-safe template
├── Dockerfile
├── docker-compose.yml      # Ollama sidecar available via --profile ollama
├── entrypoint.sh           # reads cron from config, runs supercronic
├── providers/
│   ├── base.py             # AIProvider ABC
│   ├── gemini.py           # Gemini provider
│   └── ollama.py           # Ollama provider
├── rows/
│   ├── collecting_dust.py
│   ├── easy_watch.py
│   ├── existential.py
│   ├── adrenaline.py
│   ├── quick_watch.py
│   ├── tv_collecting_dust.py
│   └── give_it_a_shot.py
└── tests/
```

---

## Adding a new row

**1. Create a row file** in `rows/`:

```python
# rows/my_row.py
from __future__ import annotations
from plex_client import MovieMeta

def filter_my_row(movies: list[MovieMeta]) -> list[MovieMeta]:
    results = [m for m in movies if not m.watched]  # your logic here
    return results
```

For AI-powered rows, accept an `AIProvider` and call `provider.categorize()`:

```python
# rows/my_ai_row.py
from __future__ import annotations
from providers.base import AIProvider
from plex_client import MovieMeta

MY_PROMPT = """
You are curating a "My Row" collection for a small, personally curated Plex library.
Select films that... [describe the vibe].
Return ONLY a raw JSON array of ratingKey integers. Example: [123, 456]
""".strip()

def filter_my_ai_row(movies: list[MovieMeta], provider: AIProvider) -> list[int]:
    payload = [
        {"ratingKey": m.ratingKey, "title": m.title, "year": m.year,
         "summary": m.summary, "genres": m.genres}
        for m in movies
    ]
    return provider.categorize(payload, MY_PROMPT)
```

**2. Wire it into `curator.py`**:

```python
from rows.my_row import filter_my_row

COLLECTION_MY_ROW = "My Row"

# inside run(), in the Movies section:
my_cfg = rows_cfg.get("my_row", {})
if _enabled(my_cfg):
    keys = _pick([m.ratingKey for m in filter_my_row(movies)], movie_seen, max_results)
    logger.info("My Row: %d items", len(keys))
    client.upsert_collection(COLLECTION_MY_ROW, keys)
```

**3. Add it to `config.yaml`**:

```yaml
rows:
  my_row:
    enabled: true
```
