# Projectionist

![Tests](https://github.com/rafvasq/projectionist/actions/workflows/test.yml/badge.svg)
![Release](https://img.shields.io/github/v/release/rafvasq/projectionist)

Projectionist is a self-hosted AI concierge and curation layer for your Plex home theater. It aims to eliminate friction and elevate the media server experience by surfacing forgotten content, managing requests via a natural language WhatsApp bot, and keeping your storage clean autonomously.

---

## Features

Projectionist is comprised of four main components that work together to maintain and curate your server:

### 1. The Curator (Home Screen Curation)
Each week, the AI scans your movie library and invents creative themed collections (e.g., *"Chasing Glory, Counting Scars"* or *"Best Laid Plans..."*), picking films that fit the theme and pinning them to your Plex home screen.
- **Fresh every week**: Previous collections are deleted and new ones are created.
- **No repeats**: A cooldown ensures the same films aren't constantly pushed.

### 2. The Concierge (WhatsApp Bot)
A Flask-based WhatsApp bot (powered by Twilio and Overseerr) that allows users to request movies and shows conversationally.
- **Natural Language Parsing**: Ask for "the movie with the guy who has scissors for hands" and it will resolve the intent to *Edward Scissorhands*.
- **Letterboxd Integration**: Paste a Letterboxd list URL directly into the chat to bulk-request all movies on the list.
- **Proactive Notifications**: Notifies users automatically when their requested media is downloaded and available on Plex.

### 3. The Promoter (Weekly Digest)
A scheduled task that sends out a "Weekly Plex Digest" on WhatsApp, highlighting recently added movies and throwing in a few random "From the Vault" recommendations to encourage watching older downloads.

### 4. The Janitor (Deletion Engine)
Keeps your hard drives from filling up. It scans for movies that are older than a set number of days and haven't been watched recently, then uses Radarr to delete them. Respects a "Keep Forever" tag for your permanent library.

---

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (recommended for easy deployment)
- A Plex server
- An AI provider (Gemini or Ollama)
- Overseerr & Radarr (if using the request and deletion features)
- Twilio Account (if using the WhatsApp bot)

---

## Setup

### 1. Get the files

Download the compose and config files:

```bash
curl -LO https://github.com/rafvasq/projectionist/releases/latest/download/docker-compose.yml
curl -LO https://github.com/rafvasq/projectionist/releases/latest/download/config.example.yaml
cp config.example.yaml config.yaml
```

### 2. Edit config.yaml

Fill in your Plex URL and token. To find your Plex token, follow [this guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). 
Fill in the API keys for Gemini, Overseerr, Twilio, and Radarr depending on which features you want to use.

### 3. Start

**Gemini** (no GPU required):

```bash
docker-compose up -d
```

**Ollama** (local, self-hosted):

```bash
docker-compose --profile ollama up -d
```

Projectionist's scheduled jobs (curator, digest) will run on the configured cron schedule. The WhatsApp bot runs continuously in the background.

### Viewing logs

```bash
docker-compose logs -f projectionist
```

---

## Configuration

See `config.example.yaml` for a full list of configuration options, including settings for the WhatsApp bot, deletion engine thresholds, and AI model choices.

---

## AI providers

### Gemini (default)

No GPU required. The free tier of `gemini-3.5-flash-lite` handles a typical home library easily.

1. Get an API key at [aistudio.google.com](https://aistudio.google.com).
2. In `config.yaml`, set `provider: gemini` and fill in your `api_key` and `model`.

### Ollama (local, self-hosted)

No API costs, no data leaves your network. 7B–9B parameter models work well (`llama3`, `mistral`). Ollama runs as a sidecar in Docker — not exposed outside your machine.

GPU support requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

1. In `config.yaml`, set `provider: ollama` and fill in the `model` and `base_url` under `# Ollama`.
2. Start with the Ollama profile: `docker-compose --profile ollama up -d`
3. Pull a model: `docker-compose exec ollama ollama pull llama3`
