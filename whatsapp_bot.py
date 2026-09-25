import os
import yaml
import json
import requests
import re
import threading
import urllib.parse
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from pydantic import BaseModel, Field
from mellea import generative, start_session

app = Flask(__name__)

# Global configuration variables
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')
STATE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'whatsapp_state.json')

ALLOWED_NUMBERS = []
TWILIO_ACCOUNT_SID = None
TWILIO_AUTH_TOKEN = None
TWILIO_PHONE_NUMBER = None
OS_URL = ""
OS_API_KEY = None
AI_MODEL = "gemini/gemini-3.5-flash"
client = None
validator = None

def init_config(config_dict=None):
    global ALLOWED_NUMBERS, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
    global OS_URL, OS_API_KEY, AI_MODEL, client, validator
    
    if config_dict is None:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config_dict = yaml.safe_load(f)
        else:
            config_dict = {}

    wa_cfg = config_dict.get('whatsapp', {})
    os_cfg = config_dict.get('overseerr', {})
    ai_cfg = config_dict.get('ai', {})

    ALLOWED_NUMBERS = [n if n.startswith('whatsapp:') else f"whatsapp:{n}" for n in wa_cfg.get('allowed_numbers', [])]
    TWILIO_ACCOUNT_SID = wa_cfg.get('twilio_account_sid')
    TWILIO_AUTH_TOKEN = wa_cfg.get('twilio_auth_token')
    
    TWILIO_PHONE_NUMBER = wa_cfg.get('twilio_phone_number')
    if TWILIO_PHONE_NUMBER and not TWILIO_PHONE_NUMBER.startswith('whatsapp:'):
        TWILIO_PHONE_NUMBER = f"whatsapp:{TWILIO_PHONE_NUMBER}"

    OS_URL = os_cfg.get('url', '').rstrip('/')
    OS_API_KEY = os_cfg.get('api_key')
    
    provider = ai_cfg.get('provider', 'gemini')
    model_name = ai_cfg.get('model', 'gemini-3.5-flash')
    AI_MODEL = f"{provider}/{model_name}"

    # Inject Gemini API Key for LiteLLM (Mellea)
    if ai_cfg.get('api_key'):
        os.environ["GEMINI_API_KEY"] = ai_cfg.get('api_key')

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None
    validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None

# Initialize with real config on module load
init_config()

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def send_whatsapp_message(to_number, text):
    if not client:
        print("Missing Twilio credentials.")
        return
    try:
        msg = client.messages.create(
            body=text,
            from_=TWILIO_PHONE_NUMBER,
            to=to_number
        )
        return msg.sid
    except Exception as e:
        print(f"Failed to send WA message: {e}")

# Mellea Semantic Parsing
class MovieIntent(BaseModel):
    title: str = Field(description="The exact official title of the movie or TV show. Resolve trivia to actual titles.")
    year: int | None = Field(description="The release year if specified, else None.")

@generative
def parse_movie(text: str) -> MovieIntent:
    """You are a movie concierge. The user will give you a text query that might be an exact title, or a piece of trivia (e.g. 'the movie with the guy who has scissors for hands'). Resolve the trivia to the exact official movie or TV show title. Extract the year if provided."""
    ...

def search_overseerr_list(query):
    if not OS_API_KEY:
        print("Missing Overseerr API Key.")
        return []
    headers = {"X-Api-Key": OS_API_KEY}
    safe_query = urllib.parse.quote(query)
    resp = requests.get(f"{OS_URL}/api/v1/search?query={safe_query}", headers=headers)
    if resp.status_code == 200:
        results = resp.json().get('results', [])
        valid_results = [r for r in results if r.get('mediaType') in ['movie', 'tv']]
        return valid_results
    return []

def get_media_details(media_type, tmdb_id):
    if not OS_API_KEY:
        return {}
    headers = {"X-Api-Key": OS_API_KEY}
    resp = requests.get(f"{OS_URL}/api/v1/{media_type}/{tmdb_id}", headers=headers)
    if resp.status_code == 200:
        return resp.json()
    return {}

def request_overseerr(media_type, tmdb_id, seasons=None):
    headers = {"X-Api-Key": OS_API_KEY}
    payload = {
        "mediaType": media_type,
        "mediaId": tmdb_id,
        "is4k": False
    }
    if media_type == 'tv' and seasons:
        payload["seasons"] = seasons
    resp = requests.post(f"{OS_URL}/api/v1/request", headers=headers, json=payload)
    return resp.status_code in [200, 201]

def handle_letterboxd_list(url, sender):
    send_whatsapp_message(sender, "🔍 Scraping Letterboxd list... This may take a minute.")
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        og_type = soup.find('meta', property='og:type')
        if og_type and og_type.get('content') == 'video.movie':
            body = soup.find('body')
            tmdb_id = body.get('data-tmdb-id') if body else None
            meta_title = soup.find('meta', property='og:title')
            title = meta_title.get('content') if meta_title else 'Unknown Film'
            
            if tmdb_id:
                resp = requests.post(f"{OS_URL}/api/v1/request", headers={"X-Api-Key": OS_API_KEY}, json={"mediaType": "movie", "mediaId": int(tmdb_id), "is4k": False})
                if resp.status_code in [200, 201]:
                    send_whatsapp_message(sender, f"✅ Successfully requested *{title}* directly from Letterboxd!")
                else:
                    send_whatsapp_message(sender, f"❌ Failed to request *{title}*. It might already be requested.")
                return
            else:
                send_whatsapp_message(sender, "❌ Could not find the TMDB ID for this film on Letterboxd.")
                return
        else:
            titles = []
            for img in soup.select('.poster img'):
                alt = img.get('alt')
                if alt:
                    titles.append(alt)
                
        if not titles:
            send_whatsapp_message(sender, "❌ Could not find any movies on that list.")
            return
            
        success_titles = []
        skipped_titles = []
        failed_titles = []
        
        for title in titles:
            media_list = search_overseerr_list(title)
            if not media_list:
                failed_titles.append(title)
                continue
                
            movie = media_list[0]
            tmdb_id = movie.get('id')
            resp = requests.post(f"{OS_URL}/api/v1/request", headers={"X-Api-Key": OS_API_KEY}, json={"mediaType": "movie", "mediaId": tmdb_id, "is4k": False})
            if resp.status_code in [200, 201]:
                success_titles.append(title)
            else:
                skipped_titles.append(title)
                
        def fmt(lst):
            if not lst: return "None"
            return ", ".join(lst[:5]) + (f" (+{len(lst)-5} more)" if len(lst) > 5 else "")

        msg = (
            f"✅ Letterboxd list processed!\n\n"
            f"✅ *Requested ({len(success_titles)})*\n"
            f"⏭️ *Skipped ({len(skipped_titles)})*: {fmt(skipped_titles)}\n"
            f"❌ *Not Found ({len(failed_titles)})*: {fmt(failed_titles)}"
        )
        send_whatsapp_message(sender, msg)
    except Exception as e:
        print(f"Letterboxd error: {e}")
        send_whatsapp_message(sender, "❌ Error processing the Letterboxd list.")


@app.route('/webhook/twilio', methods=['POST'])
def receive_message():
    post_vars = request.form.to_dict()
    print("Received Twilio Webhook:", post_vars)
            
    sender = request.form.get('From', '')
    text = request.form.get('Body', '').strip()
    print(f"Sender: {sender}, Text: {text}")
    
    twiml_resp = MessagingResponse()

    if sender not in ALLOWED_NUMBERS:
        print(f"Ignored message from unauthorized number: {sender}")
        return str(twiml_resp)

    # Universal cancellation
    if text == "0":
        state = load_state()
        state[sender] = {"status": "IDLE"}
        save_state(state)
        twiml_resp.message("❌ Search cancelled. Text me another movie or show.")
        return str(twiml_resp)

    lb_match = re.search(r'(https?://(?:www\.)?(?:letterboxd\.com/(?:[^/]+/list/[^/\s]+|film/[^/\s]+)|boxd\.it/[^\s]+))', text)
    if lb_match:
        url = lb_match.group(1)
        threading.Thread(target=handle_letterboxd_list, args=(url, sender)).start()
        return str(twiml_resp)

    state = load_state()
    user_state = state.get(sender, {"status": "IDLE"})

    if user_state["status"] == "WAITING_CONFIRM":
        options = user_state.get("options", {})
        if text in options:
            selected = options[text]
            media_type = selected["mediaType"]
            tmdb_id = selected["tmdbId"]
            title = selected["title"]
            
            if media_type == "movie":
                success = request_overseerr("movie", tmdb_id)
                if success:
                    twiml_resp.message(f"🎬 Requested *{title}*! I'll notify you when it's ready.")
                else:
                    twiml_resp.message("❌ Failed to request the movie. It might already be requested.")
                state[sender] = {"status": "IDLE"}
                save_state(state)
                return str(twiml_resp)
            else:
                twiml_resp.message(f"📺 *{title}* is a TV Show.\n\nReply *1* for All Seasons\nReply *2* for First Season Only\nReply *3* for Latest Season Only\n❌ Reply *0* to Cancel")
                state[sender] = {
                    "status": "WAITING_SEASON",
                    "tmdbId": tmdb_id,
                    "title": title
                }
                save_state(state)
                return str(twiml_resp)
        else:
            twiml_resp.message("Invalid choice. Please reply with a valid number or 0 to cancel.")
            return str(twiml_resp)

    if user_state["status"] == "WAITING_SEASON":
        tmdb_id = user_state["tmdbId"]
        title = user_state["title"]
        if text in ["1", "2", "3"]:
            details = get_media_details("tv", tmdb_id)
            total_seasons = len([s for s in details.get('seasons', []) if s.get('seasonNumber', 0) > 0])
            if total_seasons == 0: total_seasons = 1
            
            seasons = []
            if text == "1": seasons = list(range(1, total_seasons + 1))
            elif text == "2": seasons = [1]
            elif text == "3": seasons = [total_seasons]
            
            success = request_overseerr("tv", tmdb_id, seasons=seasons)
            if success:
                twiml_resp.message(f"📺 Requested *{title}*! I'll notify you when it's ready.")
            else:
                twiml_resp.message("❌ Failed to request the TV show. It might already be requested.")
            state[sender] = {"status": "IDLE"}
            save_state(state)
            return str(twiml_resp)
        else:
            twiml_resp.message("Invalid choice. Reply 1, 2, 3, or 0 to cancel.")
            return str(twiml_resp)

    if user_state["status"] == "IDLE":
        # Parse text using Mellea / Gemini
        try:
            with start_session("litellm", AI_MODEL) as session:
                intent = parse_movie(m=session, text=text)
            query = intent.title
            print(f"Mellea Extracted Intent: {intent}")
        except Exception as e:
            print(f"Mellea Parse Error: {e}")
            query = text
            intent = None

        media_list = search_overseerr_list(query)
        
        # Sort by exact year match if intent.year is present
        if intent and intent.year:
            media_list.sort(key=lambda r: 0 if str(intent.year) in (r.get('releaseDate') or r.get('firstAirDate') or '') else 1)
        if not media_list:
            twiml_resp.message(f"❌ I figured out you meant '{query}', but I couldn't find it on TMDB.")
            return str(twiml_resp)
            
        options = {}
        msg_lines = []
        limit = 3
        media_list = media_list[:limit]
        
        for i, media in enumerate(media_list, 1):
            title = media.get('title') or media.get('name')
            year = (media.get('releaseDate') or media.get('firstAirDate') or '').split('-')[0]
            if not year: year = 'Unknown Year'
            tmdb_id = media.get('id')
            media_type = media.get('mediaType', 'movie')
            
            options[str(i)] = {
                "tmdbId": tmdb_id,
                "title": f"{title} ({year})",
                "mediaType": media_type
            }
            
            type_icon = "🎬" if media_type == "movie" else "📺"
            msg_lines.append(f"{i}️⃣ {title} ({year}) {type_icon}")
            
        msg = "Reply with a number to request:\n\n" + "\n".join(msg_lines) + "\n\n❌ Reply 0 to Cancel"
        twiml_resp.message(msg)
        
        state[sender] = {
            "status": "WAITING_CONFIRM",
            "options": options
        }
        save_state(state)
        return str(twiml_resp)

    return str(twiml_resp)

@app.route('/webhook/overseerr', methods=['POST'])
def overseerr_notification():
    data = request.json
    print("Received Overseerr Webhook:", data)
    notification_type = data.get('notification_type')
    
    if notification_type in ['MEDIA_AVAILABLE', 'TEST_NOTIFICATION', 'TEST']:
        subject = data.get('subject')
        if subject:
            msg = f"🍿 *{subject}* is now available to watch on Plex!"
        else:
            media = data.get('media') or {}
            title = media.get('title', 'A movie')
            year_str = f" ({media.get('year')})" if media.get('year') else ""
            msg = f"🍿 *{title}*{year_str} is now available to watch on Plex!"
            
        if notification_type in ['TEST_NOTIFICATION', 'TEST']:
            msg = "👋 This is a test notification from Overseerr via Twilio!"
            
        for number in ALLOWED_NUMBERS:
            send_whatsapp_message(number, msg)
            
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
