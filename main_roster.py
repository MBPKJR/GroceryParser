import time
import threading
import json
import os
from email_service import EmailService
from excel_parser import parse_roster
from calendar_gen import generate_ics
from web_server import run_server

CONFIG_FILE = "config.json"
ICS_OUTPUT = "schedule.ics"
DOWNLOAD_DIR = "downloads"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        # Create a default config if it doesn't exist
        default_config = {
            "emails": [
                {
                    "username": "user@domain.de",
                    "app_password": "password",
                    "imap_server": "imap.ionos.de",
                    "imap_port": 993
                }
            ],
            "target_name": "Dreischenkemper", # Default target name
            "check_interval_seconds": 3600
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=2)
        return default_config
    
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def sync_roster():
    config = load_config()
    email_service = EmailService(config)
    
    while True:
        # Check if current time matches target times
        current_time = time.strftime("%H:%M")
        target_times = config.get("check_times", ["05:00", "22:00"])
        if current_time in target_times:
            source_type = config.get("source_type", "email")

            
            if source_type == "ukg_ics":
                ukg_url = config.get("ukg_ics_url")
                if ukg_url:
                    print(f"Lade UKG Pro Kalenderabo herunter: {ukg_url}")
                    try:
                        import requests
                        r = requests.get(ukg_url, timeout=30)
                        if r.status_code == 200:
                            with open(ICS_OUTPUT, "wb") as f:
                                f.write(r.content)
                            print(f"UKG Pro Kalender erfolgreich aktualisiert: {ICS_OUTPUT}")
                        else:
                            print(f"Fehler beim Laden des UKG Pro Kalenders (Status-Code: {r.status_code})")
                    except Exception as e:
                        print(f"Fehler bei UKG Pro ICS Synchronisation: {e}")
                else:
                    print("Warnung: 'source_type' ist 'ukg_ics', aber 'ukg_ics_url' ist nicht in der config.json konfiguriert!")
            else:
                # 1. Fetch latest roster from email
                new_files = email_service.fetch_latest_roster(DOWNLOAD_DIR)
                
                # 2. Find the latest file in the download directory
                files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith(('.xls', '.xlsx'))]
                if files:
                    latest_file = max(files, key=os.path.getmtime)
                    print(f"Verarbeite Dienstplan: {latest_file}")
                    
                    # 3. Parse the Excel file
                    shifts = parse_roster(latest_file, config.get("target_name", ""))
                    
                    if shifts:
                        print(f"{len(shifts)} Schichten gefunden.")
                        # 4. Generate ICS
                        generate_ics(shifts, ICS_OUTPUT)
                        print(f"Kalender aktualisiert: {ICS_OUTPUT}")
            
            # Sleep for a minute to avoid double triggers
            time.sleep(61)
        
        # Check every 30 seconds
        time.sleep(30)


if __name__ == "__main__":
    # Start sync loop in a background thread
    sync_thread = threading.Thread(target=sync_roster, daemon=True)
    sync_thread.start()
    
    # Start web server in the main thread
    print("Starte Webserver auf Port 8080...")
    run_server(port=8080)
