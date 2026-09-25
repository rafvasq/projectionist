import os
import yaml
import json
import logging
import requests
from datetime import datetime
from twilio.rest import Client

from plex_client import from_config

logger = logging.getLogger(__name__)

import argparse

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-deletion", action="store_true")
    parser.add_argument("--send-notification", action="store_true")
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
        
    del_cfg = cfg.get("deletion", {})
    if not del_cfg.get("enabled", False):
        logger.info("Deletion engine is disabled in config. Exiting.")
        return
        
    summary_file = os.path.join(os.path.dirname(__file__), 'data', 'deleted_summary.json')
        
    if args.send_notification:
        if not os.path.exists(summary_file):
            logger.info("No deletion summary found to send.")
            return
            
        with open(summary_file, 'r') as f:
            summary = json.load(f)
            
        deleted_titles = summary.get("deleted_titles", [])
        failed_titles = summary.get("failed_titles", [])
        dry_run = summary.get("dry_run", True)
        
        wa_cfg = cfg.get("whatsapp", {})
        if not wa_cfg.get("enabled", False) or not deleted_titles:
            os.remove(summary_file)
            return
            
        client = Client(wa_cfg.get("twilio_account_sid"), wa_cfg.get("twilio_auth_token"))
        phone_number = wa_cfg.get("twilio_phone_number")
        if phone_number and not phone_number.startswith("whatsapp:"):
            phone_number = f"whatsapp:{phone_number}"
            
        def fmt(lst):
            return "\n".join([f"- {t}" for t in lst])
            
        msg = f"🧹 *Deletion Engine Summary*\n\n"
        if dry_run:
            msg += "⚠️ *DRY RUN MODE* (No files were actually deleted)\n\n"
            
        msg += f"✅ *Removed ({len(deleted_titles)})*\n{fmt(deleted_titles)}\n"
        
        if failed_titles:
            msg += f"\n❌ *Failed ({len(failed_titles)})*\n{fmt(failed_titles)}"
            
        for number in wa_cfg.get("allowed_numbers", []):
            if not number.startswith("whatsapp:"):
                number = f"whatsapp:{number}"
            try:
                client.messages.create(body=msg, from_=phone_number, to=number)
                logger.info(f"Sent summary to {number}")
            except Exception as e:
                logger.error(f"Failed to send WA message: {e}")
                
        os.remove(summary_file)
        return
        
    if args.run_deletion:
        dry_run = del_cfg.get("dry_run", True)
        max_age_days = del_cfg.get("max_age_days", 120)
        protect_tag = del_cfg.get("protect_tag", "Keep Forever")
        
        radarr_url = cfg.get("radarr", {}).get("url", "").rstrip("/")
        radarr_key = cfg.get("radarr", {}).get("api_key", "")
        
        if not radarr_url or not radarr_key:
            logger.error("Radarr URL and API Key must be configured to run the cleaner.")
            return

        logger.info(f"Starting cleaner (Dry Run: {dry_run})")
        
        plex = from_config(cfg)
        plex.connect()
        
        movies = plex.fetch_movies()
        now = datetime.now()
        
        to_delete = []
        
        for m in movies:
            if protect_tag in m.collections or protect_tag in m.labels:
                continue
                
            if m.last_viewed_at and (now - m.last_viewed_at).days <= 7:
                continue
                
            if m.added_at:
                age_days = (now - m.added_at).days
                if age_days >= max_age_days:
                    to_delete.append(m)
                    
        if not to_delete:
            logger.info("No movies met the deletion criteria today.")
            return
            
        deleted_titles = []
        failed_titles = []
        
        headers = {"X-Api-Key": radarr_key}
        
        for m in to_delete:
            if not m.tmdb_id:
                logger.warning(f"Could not find TMDB ID for '{m.title}' — skipping")
                failed_titles.append(f"{m.title} (No TMDB ID)")
                continue
                
            logger.info(f"Checking Radarr for '{m.title}' (TMDB: {m.tmdb_id})...")
            try:
                # Lookup in Radarr by TMDB ID
                resp = requests.get(f"{radarr_url}/api/v3/movie?tmdbId={m.tmdb_id}", headers=headers)
                if resp.status_code == 200:
                    radarr_movies = resp.json()
                    if not radarr_movies:
                        logger.warning(f"'{m.title}' not found in Radarr.")
                        failed_titles.append(f"{m.title} (Not in Radarr)")
                        continue
                        
                    radarr_movie = radarr_movies[0]
                    radarr_id = radarr_movie.get("id")
                    
                    if dry_run:
                        logger.info(f"[DRY RUN] Would delete '{m.title}' (Radarr ID: {radarr_id})")
                        deleted_titles.append(m.title)
                    else:
                        logger.info(f"[ACTIVE] Deleting '{m.title}'...")
                        del_resp = requests.delete(
                            f"{radarr_url}/api/v3/movie/{radarr_id}?deleteFiles=true&addImportExclusion=false",
                            headers=headers
                        )
                        if del_resp.status_code in [200, 202]:
                            deleted_titles.append(m.title)
                        else:
                            logger.error(f"Failed to delete '{m.title}': {del_resp.status_code}")
                            failed_titles.append(f"{m.title} (API Error)")
                else:
                    logger.error(f"Failed to lookup '{m.title}': HTTP {resp.status_code}")
                    failed_titles.append(f"{m.title} (API Error)")
            except Exception as e:
                logger.error(f"Exception deleting '{m.title}': {e}")
                failed_titles.append(f"{m.title} (Exception)")

        with open(summary_file, 'w') as f:
            json.dump({
                "deleted_titles": deleted_titles,
                "failed_titles": failed_titles,
                "dry_run": dry_run
            }, f)
        logger.info("Deletion complete. Summary saved for notification.")

if __name__ == "__main__":
    run()
