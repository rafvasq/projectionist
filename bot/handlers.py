"""
bot/handlers.py
Flask blueprint and webhook request handlers:
- Twilio WhatsApp webhook (/whatsapp and /webhook/twilio)
- Overseerr notification webhook (/overseerr and /webhook/overseerr)
- Slash commands (/keep, /unkeep, /leaving, /status, /help) dispatched via bot.commands
- Conversation state machine (IDLE -> WAITING_CONFIRM / WAITING_SEASON / WAITING_LB_COLLECTION / WAITING_KEEP_CHOICE)
- Natural language query resolution via core.ai
- Letterboxd scraping and collection processing delegation
NOTE: Unused 'abort' import has been completely removed.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional

from flask import Blueprint, current_app, jsonify, request
from twilio.twiml.messaging_response import MessagingResponse

from core.ai import ai_session, read_movie
from core.notifier import normalize_whatsapp_number
from core.storage import (
    load_whatsapp_state,
    save_whatsapp_state,
)
from services.letterboxd import (
    build_list_prompt,
    extract_letterboxd_film_metadata,
    fetch_letterboxd_html,
    scrape_letterboxd_list,
)
from bot.commands import CommandContext, apply_drop, apply_keep_change, dispatch, parse_command
from bot.security import overseerr_auth_required, twilio_auth_required
from core.roles import ADMIN, numbers, role_of

logger = logging.getLogger(__name__)

bot_bp = Blueprint("bot", __name__)



def process_letterboxd_collection(
    sender: str,
    list_title: str,
    titles: list[Any],
    create_collection: bool = True,
) -> None:
    """
    Process Letterboxd collection: query Overseerr, add items to Plex collection,
    label with 'Keep Forever', and persist in tracked collections.
    Delegates to CollectionTracker.process_letterboxd_collection.
    """
    from bot.app import get_services
    services = get_services()
    tracker = services.get("tracker")
    notifier = services.get("notifier")
    if tracker:
        tracker.process_letterboxd_collection(
            sender=sender,
            list_title=list_title,
            titles=titles,
            create_collection=create_collection,
            notifier=notifier,
        )


# ==============================================================================
# Async Background Task Handlers
# ==============================================================================

def process_search_async(app: Any, sender: str, text: str) -> None:
    """Perform natural language parsing and Overseerr query in background thread."""
    with app.app_context():
        from bot.app import get_services
        services = get_services()
        ai_model = services.get("ai_model")
        overseerr = services.get("overseerr")
        notifier = services.get("notifier")

        # 1. AI Intent Extraction via Mellea
        try:
            with ai_session(ai_model) as session:
                intent = read_movie(m=session, text=text)
            query = intent.title
            logger.info("Mellea Extracted Intent: %s", intent)
        except Exception as e:
            logger.warning("Mellea Parse Error: %s", e)
            query = text
            intent = None

        if not overseerr:
            if notifier:
                notifier.send_message(sender, "❌ Service unavailable. Could not connect to Overseerr.")
            return

        media_list = overseerr.search(query)
        if media_list is None:
            if notifier:
                notifier.send_message(sender, "❌ Service unavailable. Could not connect to Overseerr.")
            return

        if intent and intent.year:
            media_list.sort(
                key=lambda r: 0 if str(intent.year) in (r.get("releaseDate") or r.get("firstAirDate") or "") else 1
            )

        if not media_list:
            if notifier:
                notifier.send_message(sender, f"❌ I figured out you meant '{query}', but I couldn't find it on TMDB.")
            return

        options = {}
        msg_lines = []
        for i, media in enumerate(media_list[:3], 1):
            title = media.get("title") or media.get("name")
            year = (media.get("releaseDate") or media.get("firstAirDate") or "").split("-")[0]
            if not year:
                year = "Unknown Year"
            tmdb_id = media.get("id")
            media_type = media.get("mediaType", "movie")

            options[str(i)] = {
                "tmdbId": tmdb_id,
                "title": f"{title} ({year})",
                "mediaType": media_type,
            }
            type_icon = "🎬" if media_type == "movie" else "📺"
            msg_lines.append(f"{i}️⃣ {title} ({year}) {type_icon}")

        msg = "Reply with a number to request:\n\n" + "\n".join(msg_lines) + "\n\n❌ Reply 0 to Cancel"

        state = load_whatsapp_state()
        state[sender] = {
            "status": "WAITING_CONFIRM",
            "options": options,
        }
        save_whatsapp_state(state)

        if notifier:
            notifier.send_message(sender, msg)


def handle_letterboxd_url_async(app: Any, url: str, sender: str, is_admin: bool = True) -> None:
    """Handle an incoming Letterboxd list or film URL asynchronously. Lists are admin-only."""
    with app.app_context():
        from bot.app import get_services
        services = get_services()
        overseerr = services.get("overseerr")
        notifier = services.get("notifier")

        if notifier:
            notifier.send_message(sender, "🔍 Scraping Letterboxd... This may take a minute.")

        try:
            list_url = url if "/list/" in url else None
            if list_url is None:
                # Direct film or redirect inspection (short links can point at either)
                html, resolved_url = fetch_letterboxd_html(url)
                if html and "/list/" not in resolved_url:
                    tmdb_id, title = extract_letterboxd_film_metadata(html)
                    if tmdb_id and overseerr:
                        success = overseerr.request_media("movie", int(tmdb_id))
                        msg = (
                            f"✅ Successfully requested *{title}* directly from Letterboxd!"
                            if success
                            else f"❌ Failed to request *{title}*. It might already be requested."
                        )
                    else:
                        msg = "❌ Could not find the TMDB ID for this film on Letterboxd."
                    if notifier:
                        notifier.send_message(sender, msg)
                    return
                list_url = resolved_url if html else url

            if not is_admin:
                if notifier:
                    notifier.send_message(sender, "📋 List imports are admin-only. Film links work for everyone.")
                return
            list_title, titles = scrape_letterboxd_list(list_url)

            if not titles:
                if notifier:
                    notifier.send_message(sender, "❌ Could not find any movies on that list.")
                return

            state = load_whatsapp_state()
            state[sender] = {
                "status": "WAITING_LB_COLLECTION",
                "url": url,
                "list_title": list_title,
                "titles": titles,
            }
            save_whatsapp_state(state)

            prompt_msg = build_list_prompt(list_title, len(titles))
            if notifier:
                notifier.send_message(sender, prompt_msg)

        except Exception as e:
            logger.error("Letterboxd processing error: %s", e)
            if notifier:
                notifier.send_message(sender, "❌ Error processing the Letterboxd link.")


def run_collection_process_async(
    app: Any, sender: str, list_title: str, titles: list[Any], create_collection: bool
) -> None:
    with app.app_context():
        process_letterboxd_collection(sender, list_title, titles, create_collection)


def _command_context(sender: str) -> tuple[CommandContext, Any]:
    from bot.app import get_services
    services = get_services()
    ctx = CommandContext(
        sender=sender,
        plex=services.get("plex"),
        config=services.get("config", {}),
        radarr=services.get("radarr"),
    )
    return ctx, services.get("notifier")


def run_command_async(app: Any, sender: str, text: str) -> None:
    """Run a slash command in a background thread and reply via the notifier."""
    with app.app_context():
        ctx, notifier = _command_context(sender)
        reply = dispatch(text, ctx)
        if reply and notifier:
            notifier.send_message(sender, reply)


def run_keep_choice_async(app: Any, sender: str, action: str, selected: dict[str, Any]) -> None:
    """Apply a numbered /keep or /unkeep selection and reply via the notifier."""
    with app.app_context():
        ctx, notifier = _command_context(sender)
        if action == "drop":
            reply = apply_drop(ctx, selected["title"], selected.get("unprotect", False))
        else:
            reply = apply_keep_change(ctx, action, selected.get("ratingKey"), selected.get("title", ""))
        if notifier:
            notifier.send_message(sender, reply)


# ==============================================================================
# Webhook Routes
# ==============================================================================

@bot_bp.route("/whatsapp", methods=["POST"])
@bot_bp.route("/webhook/twilio", methods=["POST"])
@twilio_auth_required()
def receive_whatsapp():
    """
    Twilio WhatsApp Webhook endpoint (Twilio signature verified).
    Processes user commands, natural language queries, and state machine replies.
    """
    from bot.app import get_services
    services = get_services()
    cfg = services.get("config", {})
    overseerr = services.get("overseerr")

    sender = request.form.get("From", "")
    text = request.form.get("Body", "").strip()
    logger.info("Received WhatsApp message from %s: %s", sender, text)

    twiml_resp = MessagingResponse()

    # 1. Authorization Verification
    role = role_of(sender, current_app.config.get("USERS", {}))
    if role is None:
        logger.info("Ignored message from unauthorized number: %s", sender)
        return str(twiml_resp)

    # 2. Universal Cancellation
    if text == "0":
        state = load_whatsapp_state()
        state[sender] = {"status": "IDLE"}
        save_whatsapp_state(state)
        twiml_resp.message("❌ Search cancelled. Text me another movie or show.")
        return str(twiml_resp)

    # 3. Slash Commands (a leading "/" is required, so titles like "Keep the Lights On" never match)
    if parse_command(text):
        if role != ADMIN:
            twiml_resp.message("Commands are for admins. Just tell me what you'd like to watch.")
            return str(twiml_resp)
        state = load_whatsapp_state()
        state[sender] = {"status": "IDLE"}
        save_whatsapp_state(state)
        app_obj = current_app._get_current_object()
        threading.Thread(
            target=run_command_async,
            args=(app_obj, sender, text),
            daemon=True,
        ).start()
        return str(twiml_resp)

    # 4. Letterboxd URL Detection
    lb_match = re.search(
        r"(https?://(?:www\.)?(?:letterboxd\.com/(?:[^/]+/list/[^/\s]+|film/[^/\s]+)|boxd\.it/[^\s]+))",
        text,
    )
    if lb_match:
        url = lb_match.group(1)
        app_obj = current_app._get_current_object()
        threading.Thread(
            target=handle_letterboxd_url_async,
            args=(app_obj, url, sender, role == ADMIN),
            daemon=True,
        ).start()
        return str(twiml_resp)

    # 5. State Machine Transitions
    state = load_whatsapp_state()
    user_state = state.get(sender, {"status": "IDLE"})

    # State: WAITING_KEEP_CHOICE
    if user_state.get("status") == "WAITING_KEEP_CHOICE":
        options = user_state.get("options", {})
        if text in options:
            action = user_state.get("action", "keep")
            selected = options[text]
            state[sender] = {"status": "IDLE"}
            save_whatsapp_state(state)
            app_obj = current_app._get_current_object()
            threading.Thread(
                target=run_keep_choice_async,
                args=(app_obj, sender, action, selected),
                daemon=True,
            ).start()
            return str(twiml_resp)
        else:
            if text.isdigit():
                twiml_resp.message("Invalid choice. Please reply with a valid number or 0 to cancel.")
                return str(twiml_resp)
            else:
                state[sender] = {"status": "IDLE"}
                save_whatsapp_state(state)
                user_state = state[sender]

    # State: WAITING_LB_COLLECTION
    if user_state.get("status") == "WAITING_LB_COLLECTION":
        list_title = user_state.get("list_title", "Letterboxd List")
        titles = user_state.get("titles", [])
        if text == "1":
            state[sender] = {"status": "IDLE"}
            save_whatsapp_state(state)
            twiml_resp.message(
                f"🎬 Processing *{list_title}* ({len(titles)} films) for Plex Collection & Overseerr download..."
            )
            app_obj = current_app._get_current_object()
            threading.Thread(
                target=run_collection_process_async,
                args=(app_obj, sender, list_title, titles, True),
                daemon=True,
            ).start()
            return str(twiml_resp)
        elif text == "2":
            state[sender] = {"status": "IDLE"}
            save_whatsapp_state(state)
            twiml_resp.message(f"📥 Processing *{list_title}* ({len(titles)} films) for download...")
            app_obj = current_app._get_current_object()
            threading.Thread(
                target=run_collection_process_async,
                args=(app_obj, sender, list_title, titles, False),
                daemon=True,
            ).start()
            return str(twiml_resp)
        else:
            if text.isdigit():
                twiml_resp.message(
                    "Invalid choice. Reply *1* for Collection + Download, *2* for Download only, or *0* to cancel."
                )
                return str(twiml_resp)
            else:
                state[sender] = {"status": "IDLE"}
                save_whatsapp_state(state)
                user_state = state[sender]

    # State: WAITING_CONFIRM
    if user_state.get("status") == "WAITING_CONFIRM":
        options = user_state.get("options", {})
        if text in options:
            selected = options[text]
            media_type = selected.get("mediaType", "movie")
            tmdb_id = selected.get("tmdbId")
            title = selected.get("title", "")

            if media_type == "movie":
                success = overseerr.request_media("movie", tmdb_id) if overseerr else False
                if success:
                    twiml_resp.message(f"🎬 Requested *{title}*! I'll notify you when it's ready.")
                else:
                    twiml_resp.message("❌ Failed to request the movie. It might already be requested.")
                state[sender] = {"status": "IDLE"}
                save_whatsapp_state(state)
                return str(twiml_resp)
            else:
                twiml_resp.message(
                    f"📺 *{title}* is a TV Show.\n\n"
                    f"Reply *1* for All Seasons\n"
                    f"Reply *2* for First Season Only\n"
                    f"Reply *3* for Latest Season Only\n"
                    f"❌ Reply *0* to Cancel"
                )
                state[sender] = {
                    "status": "WAITING_SEASON",
                    "tmdbId": tmdb_id,
                    "title": title,
                }
                save_whatsapp_state(state)
                return str(twiml_resp)
        else:
            if text.isdigit():
                twiml_resp.message("Invalid choice. Please reply with a valid number or 0 to cancel.")
                return str(twiml_resp)
            else:
                state[sender] = {"status": "IDLE"}
                save_whatsapp_state(state)
                user_state = state[sender]

    # State: WAITING_SEASON
    if user_state.get("status") == "WAITING_SEASON":
        tmdb_id = user_state.get("tmdbId")
        title = user_state.get("title", "")
        if text in ["1", "2", "3"]:
            details = overseerr.get_media_details("tv", tmdb_id) if overseerr else {}
            total_seasons = len([s for s in details.get("seasons", []) if s.get("seasonNumber", 0) > 0])
            if total_seasons == 0:
                total_seasons = 1

            if text == "1":
                seasons = list(range(1, total_seasons + 1))
            elif text == "2":
                seasons = [1]
            elif text == "3":
                seasons = [total_seasons]
            else:
                seasons = [1]

            success = overseerr.request_media("tv", tmdb_id, seasons=seasons) if overseerr else False
            if success:
                twiml_resp.message(f"📺 Requested *{title}*! I'll notify you when it's ready.")
            else:
                twiml_resp.message("❌ Failed to request the TV show. It might already be requested.")
            state[sender] = {"status": "IDLE"}
            save_whatsapp_state(state)
            return str(twiml_resp)
        else:
            if text.isdigit():
                twiml_resp.message("Invalid choice. Reply 1, 2, 3, or 0 to cancel.")
                return str(twiml_resp)
            else:
                state[sender] = {"status": "IDLE"}
                save_whatsapp_state(state)
                user_state = state[sender]

    # State: IDLE
    if user_state.get("status") == "IDLE":
        app_obj = current_app._get_current_object()
        threading.Thread(
            target=process_search_async,
            args=(app_obj, sender, text),
            daemon=True,
        ).start()
        twiml_resp.message("🔍 Searching...")
        return str(twiml_resp)

    return str(twiml_resp)


@bot_bp.route("/overseerr", methods=["POST"])
@bot_bp.route("/webhook/overseerr", methods=["POST"])
@overseerr_auth_required()
def receive_overseerr():
    """
    Overseerr Webhook endpoint (shared secret verified).
    Notifies WhatsApp users when requested media finishes downloading and becomes available.
    Reconciles newly available items against active tracked collections.
    """
    from bot.app import get_services
    services = get_services()
    cfg = services.get("config", {})
    notifier = services.get("notifier")
    tracker = services.get("tracker")

    data = request.json or {}
    logger.info("Received Overseerr Webhook: %s", data)
    notification_type = data.get("notification_type")

    if notification_type in ["MEDIA_AVAILABLE", "TEST_NOTIFICATION", "TEST"]:
        subject = data.get("subject")
        media = data.get("media") or {}
        tmdb_id = media.get("tmdbId") or data.get("tmdbId")

        if subject:
            msg = f"🍿 *{subject}* is now available to watch on Plex!"
            title_for_var = subject
        else:
            title = media.get("title", "A movie")
            year_str = f" ({media.get('year')})" if media.get("year") else ""
            msg = f"🍿 *{title}*{year_str} is now available to watch on Plex!"
            title_for_var = f"{title}{year_str}"

        content_vars = {"1": title_for_var}

        if notification_type in ["TEST_NOTIFICATION", "TEST"]:
            msg = "👋 This is a test notification from Overseerr via Twilio!"
            content_vars = {"1": "Test Notification"}

        collection_notified = False
        if notification_type == "MEDIA_AVAILABLE" and tmdb_id and tracker:
            try:
                title_hint = media.get("title") or subject
                matched = tracker.handle_media_available(int(tmdb_id), title_hint=title_hint)
                if matched:
                    collection_notified = True
                    for match in matched:
                        coll_title = match["collection_title"]
                        ready_count = match["ready_count"]
                        total_count = match["total_count"]
                        target_sender = match.get("sender")

                        coll_msg = (
                            f"🍿 *{title_for_var}* is now available on Plex and added to your "
                            f"*{coll_title}* collection! ({ready_count}/{total_count} ready)"
                        )
                        if ready_count == total_count:
                            coll_msg += (
                                f"\n\n🎉 All {total_count} films in *{coll_title}* are now "
                                f"downloaded and in your Plex collection!"
                            )

                        # Progress goes to whoever imported the list; if they are no longer allowed, to admins
                        users = current_app.config.get("USERS", {})
                        norm_target = normalize_whatsapp_number(target_sender)
                        recipients = [norm_target] if norm_target in users else numbers(users, ADMIN)
                        if notifier:
                            for number in recipients:
                                notifier.send_message(to=number, body=coll_msg)
            except Exception as e:
                logger.error("Error handling media available for collections: %s", e)

        if not collection_notified and notifier:
            template_sid = cfg.get("whatsapp", {}).get("availability_template_sid")
            notifier.send_broadcast(
                body=msg,
                content_sid=template_sid,
                content_variables=content_vars if template_sid else None,
            )

    return "OK", 200


@bot_bp.route("/health", methods=["GET"])
def health_check():
    """Liveness probe endpoint."""
    return jsonify({"status": "ok", "service": "projectionist-bot"}), 200
