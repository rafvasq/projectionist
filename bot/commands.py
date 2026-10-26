"""
bot/commands.py
Admin slash commands for the WhatsApp bot: /keep, /unkeep, /leaving, /status, /help.

Only a leading "/" triggers a command, so titles like "Keep the Lights On" still go
through the normal request flow.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

from bot import answers
from core.flagged import dequeue
from core.storage import (
    load_cleaner_runs, load_tracked_collections, load_whatsapp_state, save_tracked_collections, save_whatsapp_state,
)

logger = logging.getLogger(__name__)

KEEP_LABEL = "Keep Forever"
PROTECT_LABELS = {"keep", "keep forever"}
MAX_CHOICES = 5
FUZZY_CUTOFF = 0.78
_KEYCAP = "️⃣"
_PLEX_DOWN = "❌ Plex isn't available right now."


@dataclass
class CommandContext:
    sender: str
    plex: Any
    config: dict[str, Any]
    radarr: Any = None


# ==============================================================================
# Parsing & matching
# ==============================================================================

def parse_command(text: str) -> Optional[tuple[str, str]]:
    """Return (name, args) if text is a slash command, else None. Name is lowercased."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text[1:].split(None, 1)
    return (parts[0].lower() if parts else ""), (parts[1].strip() if len(parts) > 1 else "")


def split_title_year(args: str) -> tuple[str, Optional[int]]:
    """Split a trailing year, e.g. 'Heat 1995' or 'Heat (1995)', from a title query."""
    match = re.match(r"^(.*?)\s*\(?((?:19|20)\d{2})\)?$", args.strip())
    if match and match.group(1).strip():
        return match.group(1).strip(), int(match.group(2))
    return args.strip(), None


def _normalize(value: str) -> str:
    """Lowercase, fold accents, drop punctuation and a leading article ('The Heat' -> 'heat')."""
    folded = unicodedata.normalize("NFKD", (value or "").casefold())
    tokens = re.sub(r"[^\w\s]", " ", "".join(c for c in folded if not unicodedata.combining(c))).split()
    if len(tokens) > 1 and tokens[0] in {"the", "a", "an"}:
        tokens = tokens[1:]
    return " ".join(tokens)


def _match(index: list[tuple[Any, str]], title: str, year: Optional[int], fuzzy: bool = True) -> tuple[list[Any], bool]:
    """Return (movies, exact): exact title, else all words in any order, else fuzzy (typos)."""
    needle = _normalize(title)
    if not needle:
        return [], False
    pool = [(m, n) for m, n in index if year is None or m.year == year]
    order = lambda m: (_normalize(m.title), m.year or 0)  # noqa: E731

    exact = [m for m, n in pool if n == needle]
    if exact:
        return sorted(exact, key=order), True
    words = set(needle.split())
    found = [m for m, n in pool if words <= set(n.split())]
    if found:
        return sorted(found, key=order), False
    if not fuzzy:
        return [], False
    scored = sorted(
        ((difflib.SequenceMatcher(None, needle, n).ratio(), m) for m, n in pool),
        key=lambda pair: (-pair[0], order(pair[1])),
    )
    return [m for ratio, m in scored if ratio >= FUZZY_CUTOFF], False


def find_movies(movies: list[Any], query: str) -> tuple[list[Any], bool]:
    """Match a '<title> [year]' query against the library. Returns (movies, exact)."""
    index = [(m, _normalize(m.title)) for m in movies]
    title, year = split_title_year(query)
    found, exact = _match(index, title, year)
    if not found and year is not None:
        # The number may belong to the title ('Blade Runner 2049'). Don't fuzz that retry,
        # so an explicit year still narrows typo matches.
        found, exact = _match(index, query, None, fuzzy=False)
    return found, exact


_label = answers.movie_label


def _protect_labels(movie: Any) -> list[str]:
    return [l for l in (movie.labels or []) if l.strip().lower() in PROTECT_LABELS]


def _library(ctx: CommandContext) -> tuple[Optional[list[Any]], Optional[str]]:
    """Fresh movie list, or an error reply as the second item."""
    if ctx.plex is None:
        return None, _PLEX_DOWN
    try:
        return ctx.plex.fetch_movies(force_refresh=True), None
    except Exception as exc:
        logger.error("Could not fetch Plex movies: %s", exc)
        return None, "❌ Couldn't reach Plex."


# ==============================================================================
# /keep and /unkeep
# ==============================================================================

def _keep_change(ctx: CommandContext, args: str, action: str) -> str:
    verb = f"/{action}"
    if not args:
        return f"Usage: {verb} <title> [year]\nExample: {verb} Heat 1995"
    movies, error = _library(ctx)
    if error:
        return error

    matches, exact = find_movies(movies, args)
    if not matches:
        return f"❌ No movie matching '{args}' in your Plex library. {verb} only works on movies you already have."

    candidates = matches
    if action == "unkeep":
        candidates = [m for m in matches if _protect_labels(m)]
        if not candidates:
            if len(matches) > 1:
                return f"ℹ️ None of the {len(matches)} matches for '{args}' are tagged Keep Forever."
            return f"ℹ️ *{_label(matches[0])}* isn't tagged Keep Forever."

    # Apply a single exact title match straight away; anything looser is confirmed first.
    if exact and len(candidates) == 1:
        return apply_keep_change(ctx, action, candidates[0].ratingKey, _label(candidates[0]))

    shown = candidates[:MAX_CHOICES]
    options = {str(i): {"ratingKey": m.ratingKey, "title": _label(m)} for i, m in enumerate(shown, 1)}
    state = load_whatsapp_state()
    state[ctx.sender] = {"status": "WAITING_KEEP_CHOICE", "action": action, "options": options}
    save_whatsapp_state(state)

    if len(shown) == 1:
        return f"Did you mean *{_label(shown[0])}*?\n\nReply *1* to {action} it\n❌ Reply 0 to Cancel"
    lines = [f"{i}{_KEYCAP} {o['title']}" for i, o in options.items()]
    if len(candidates) > len(shown):
        lines.append(f"…and {len(candidates) - len(shown)} more. Add the year to narrow it down.")
    return f"Which one should I {action}?\n\n" + "\n".join(lines) + "\n\nReply with a number\n❌ Reply 0 to Cancel"


def apply_keep_change(ctx: CommandContext, action: str, rating_key: int, title: str) -> str:
    """Add or remove the Keep Forever label on one movie and describe the result."""
    if ctx.plex is None:
        return _PLEX_DOWN
    try:
        if action == "keep":
            if ctx.plex.add_label([rating_key], KEEP_LABEL):
                saved = " It's off the deletion queue." if dequeue(rating_key) else ""
                return f"🔒 *{title}* is now tagged Keep Forever.{saved}"
            return f"❌ Couldn't tag *{title}*. Check the logs."

        movies, error = _library(ctx)
        if error:
            return error
        movie = next((m for m in movies if m.ratingKey == rating_key), None)
        if movie is None:
            return f"❌ *{title}* is no longer in your Plex library."
        labels = _protect_labels(movie)
        if not labels:
            return f"ℹ️ *{title}* isn't tagged Keep Forever."
        if sum(ctx.plex.remove_label([rating_key], label) for label in labels) < len(labels):
            return f"❌ Couldn't fully untag *{title}*. Check the logs."
        return f"🔓 *{title}* is no longer tagged Keep Forever."
    except Exception as exc:
        logger.error("Error applying /%s to %s: %s", action, title, exc)
        return f"❌ Something went wrong updating *{title}*."


# ==============================================================================
# /drop
# ==============================================================================

LEAVING_SOON = "Leaving Soon"


def cmd_drop(ctx: CommandContext, args: str) -> str:
    if not args:
        return "Usage: /drop <collection>\nExample: /drop Girl Bands"
    if ctx.plex is None:
        return _PLEX_DOWN
    try:
        names = [n for n in ctx.plex.list_collections() if n != LEAVING_SOON]
        found, _ = _match([(c, _normalize(c.title)) for c in map(_named, names)], args, None)
        if not found:
            return f"❌ No collection matching '{args}'."
        if len(found) > 1:
            return "Several collections match, be more specific:\n" + "\n".join(f"• {c.title}" for c in found[:MAX_CHOICES])
        name = found[0].title
        films = len(ctx.plex.collection_keys(name))
    except Exception as exc:
        logger.error("Could not look up collections: %s", exc)
        return "❌ Couldn't reach Plex."

    pending = sum(1 for i in load_tracked_collections().get(name, {}).get("items", {}).values() if i.get("status") == "pending")
    options = {"1": {"title": name, "unprotect": False}, "2": {"title": name, "unprotect": True}}
    state = load_whatsapp_state()
    state[ctx.sender] = {"status": "WAITING_KEEP_CHOICE", "action": "drop", "options": options}
    save_whatsapp_state(state)
    note = f"\n{pending} not downloaded yet; they stay requested in Overseerr but will no longer join a collection." if pending else ""
    return (
        f"Drop the collection *{name}* ({films} films)?{note}\n\n"
        f"1{_KEYCAP} Delete it; the films stay protected\n"
        f"2{_KEYCAP} Delete it and remove Keep Forever from its films\n\n"
        "Reply with a number\n❌ Reply 0 to Cancel"
    )


def _named(title: str) -> Any:
    return SimpleNamespace(title=title, year=None)


def apply_drop(ctx: CommandContext, name: str, unprotect: bool) -> str:
    """Delete a collection, stop tracking it, and optionally take Keep Forever off its films."""
    if ctx.plex is None:
        return _PLEX_DOWN
    try:
        keys = ctx.plex.collection_keys(name)
        if not ctx.plex.delete_collection(name):
            return f"❌ *{name}* is no longer in Plex."
        tracked = load_tracked_collections()
        if tracked.pop(name, None) is not None:
            save_tracked_collections(tracked)
        if unprotect and keys and ctx.plex.remove_label(keys, KEEP_LABEL) < len(keys):
            return f"⚠️ Deleted *{name}*, but Keep Forever couldn't be removed from every film. Check the logs."
    except Exception as exc:
        logger.error("Error dropping collection %s: %s", name, exc)
        return f"❌ Something went wrong dropping *{name}*."
    return f"🗑️ Deleted *{name}*." + (f" Keep Forever removed from {len(keys)} films." if unprotect else " Its films stay protected.")


# ==============================================================================
# /leaving, /status, /help
# ==============================================================================

def cmd_leaving(ctx: CommandContext, args: str) -> str:
    movies, error = _library(ctx)
    return error or answers.leaving(movies, ctx.config)


def cmd_log(ctx: CommandContext, args: str) -> str:
    return answers.cleaner_log(load_cleaner_runs())


def cmd_recent(ctx: CommandContext, args: str) -> str:
    days = 7
    if args:
        if not args.isdigit() or not 1 <= int(args) <= 365:
            return "Usage: /recent [days]\nExample: /recent 14 (1 to 365 days, default 7)"
        days = int(args)
    movies, error = _library(ctx)
    return error or answers.recent(movies, days)


def cmd_downloads(ctx: CommandContext, args: str) -> str:
    if ctx.radarr is None or not ctx.radarr.is_configured:
        return "❌ Radarr isn't configured."
    queue, missing = ctx.radarr.get_queue(), ctx.radarr.get_missing()
    if queue is None or missing is None:
        return "❌ Couldn't reach Radarr."
    return answers.downloading(queue, missing)


def cmd_status(ctx: CommandContext, args: str) -> str:
    tracked = load_tracked_collections()
    if not tracked:
        return "No collections are waiting on downloads. Paste a Letterboxd list link to create one."
    lines = []
    for title, data in tracked.items():
        items = data.get("items", {}) if isinstance(data, dict) else {}
        ready = sum(1 for it in items.values() if isinstance(it, dict) and it.get("status") == "available")
        icon = "✅" if items and ready == len(items) else "⏳"
        lines.append(f"{icon} *{title}*: {ready}/{len(items)} ready")
    return "📁 *Collections waiting on downloads*\n" + "\n".join(lines)


def cmd_help(ctx: CommandContext, args: str) -> str:
    lines = ["*Commands*"] + [f"{usage} - {summary}" for usage, summary, _ in COMMANDS.values()]
    lines.append("\nAnything else is treated as a movie or show to request, or paste a Letterboxd link.")
    return "\n".join(lines)


# name -> (usage, summary, handler)
COMMANDS = {
    "keep": ("/keep <title> [year]", "protect a movie in Plex from cleanup", lambda c, a: _keep_change(c, a, "keep")),
    "unkeep": ("/unkeep <title> [year]", "remove Keep Forever from a movie", lambda c, a: _keep_change(c, a, "unkeep")),
    "drop": ("/drop <collection>", "delete a collection, optionally unprotecting its films", cmd_drop),
    "leaving": ("/leaving", "movies flagged for deletion", cmd_leaving),
    "log": ("/log", "the cleaner's last few runs", cmd_log),
    "recent": ("/recent [days]", "movies added lately (default 7 days)", cmd_recent),
    "downloads": ("/downloads", "what is downloading or waiting for a release", cmd_downloads),
    "status": ("/status", "collections still waiting on downloads", cmd_status),
    "help": ("/help", "show this list", cmd_help),
}


def dispatch(text: str, ctx: CommandContext) -> Optional[str]:
    """Run a slash command and return the reply text, or None if text isn't a command."""
    parsed = parse_command(text)
    if parsed is None:
        return None
    name, args = parsed
    if name not in COMMANDS:
        return f"❓ Unknown command /{name}. Try /help."
    try:
        return COMMANDS[name][2](ctx, args)
    except Exception as exc:
        logger.error("Command /%s failed: %s", name, exc)
        return f"❌ Something went wrong running /{name}."
