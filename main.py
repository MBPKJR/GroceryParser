import sys
import os
import json
import time
import datetime
import traceback
from email_client import EmailFetcher
from parser import ReceiptParser
from homeassistant import HomeAssistantClient
from paperless import PaperlessClient

def load_config(config_path="config.json"):
    if not os.path.exists(config_path):
        default_config = {
          "emails": [
            {
              "username": "email@example.com",
              "app_password": "your-app-password",
              "imap_server": "imap.gmail.com",
              "imap_port": 993,
              "folder": "INBOX"
            }
          ],
          "home_assistant": {
            "url": "http://homeassistant.local:8123",
            "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",
            "sensors": {
              "groceries": "sensor.grocery_expenses",
              "misc": "sensor.misc_expenses"
            }
          },
          "paperless": {
            "url": "http://paperless.local:8000",
            "token": "YOUR_API_TOKEN",
            "enabled": False
          }
        }
        with open(config_path, "w") as f:
            json.dump(default_config, f, indent=2)
        print(f"Konfigurationsdatei {config_path} wurde erstellt. Bitte ausfüllen!")
        sys.exit(0)
        
    with open(config_path, "r") as f:
        return json.load(f)

import socket
import traceback

# Set global socket timeout
socket.setdefaulttimeout(30)

def update_ha_health(ha_client, silent_log):
    import subprocess
    import shutil
    
    services = {
        "groceryparser.service": "groceryparser_service",
        "history_import.service": "history_import_service",
        "paperless_enricher.service": "paperless_enricher_service"
    }
    
    status_attrs = {}
    all_healthy = True
    
    # 1. Check services
    for svc_file, attr_name in services.items():
        try:
            r = subprocess.run(["systemctl", "is-active", svc_file], capture_output=True, text=True, timeout=5)
            state = r.stdout.strip()
            status_attrs[attr_name] = state
            if state != "active":
                all_healthy = False
        except Exception as e:
            silent_log(f"Error checking service {svc_file}: {e}")
            status_attrs[attr_name] = "unknown"
            all_healthy = False
            
    # 2. Gather resource stats
    # Load Avg
    try:
        with open("/proc/loadavg", "r") as f:
            load = f.read().strip().split()[:3]
            load_avg = " ".join(load)
    except Exception:
        load_avg = "0.00 0.00 0.00"
        
    # RAM Usage
    mem_used_pct = 0.0
    try:
        mem_total = 0
        mem_avail = 0
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1])
        if mem_total:
            mem_used_pct = round(((mem_total - mem_avail) / mem_total) * 100, 1)
    except Exception as e:
        silent_log(f"Error reading RAM usage: {e}")
        
    # Disk Usage
    disk_used_pct = 0.0
    try:
        usage = shutil.disk_usage("/")
        disk_used_pct = round((usage.used / usage.total) * 100, 1)
    except Exception as e:
        silent_log(f"Error reading disk usage: {e}")
        
    # Assemble attributes
    status_attrs.update({
        "last_heartbeat": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ram_usage_percent": mem_used_pct,
        "disk_usage_percent": disk_used_pct,
        "load_average": load_avg
    })
    
    # State determination
    if all_healthy:
        state = "healthy"
    else:
        if status_attrs.get("groceryparser_service") != "active":
            state = "critical"
        else:
            state = "warning"
            
    silent_log(f"Posting health update to HA. State: {state}, Stats: RAM={mem_used_pct}%, Disk={disk_used_pct}%, Load={load_avg}")
    ha_client.update_health(state, status_attrs)

def run_silent(config, daemon=False):
    def silent_log(msg):
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
        
    fetcher = EmailFetcher(config, logger_callback=silent_log)
    parser = ReceiptParser()
    ha_client = HomeAssistantClient(config)
    paperless_client = PaperlessClient(config)
    
    while True:
        current_month = datetime.datetime.now().strftime("%Y-%m")
        silent_log(f"--- HEARTBEAT: GroceryParser Loop Start ({current_month}) ---")
        
        # Update Home Assistant Health Sensor
        try:
            update_ha_health(ha_client, silent_log)
        except Exception as e:
            silent_log(f"Error updating HA health: {e}")
            
        silent_log(f"CWD: {os.getcwd()}")
        
        # Check for monthly reset
        last_month_file = "last_month.txt"
        last_month = ""
        if os.path.exists(last_month_file):
            with open(last_month_file, "r") as f:
                last_month = f.read().strip()
                
        if last_month != current_month:
            silent_log("Neuer Monat erkannt! Setze Home Assistant Sensoren auf 0 zurück...")
            # Reset HA sensors
            ha_client.add_to_total(-ha_client.get_current_total("lebensmittel"), "lebensmittel")
            ha_client.add_to_total(-ha_client.get_current_total("sonstiges"), "sonstiges")
            
            # Clear processed emails so we don't carry over old IDs unnecessarily
            if os.path.exists("processed_emails.json"):
                os.remove("processed_emails.json")
            fetcher.processed_ids = set()
            
            with open(last_month_file, "w") as f:
                f.write(current_month)
        
        silent_log("Starting check for new receipts...")
        try:
            receipt_items = fetcher.fetch_new_receipts()
            if receipt_items:
                for item in receipt_items:
                    try:
                        msg_id = item["msg_id"]
                        subject = item.get("subject", "").lower()
                        
                        # Subject Blacklist
                        blacklist = ["versand", "versendet", "shipped", "bestellbestätigung", "bestellung", "order confirmation", "order confirmed"]
                        if any(b in subject for b in blacklist):
                            silent_log(f"Skipping email due to subject blacklist: {item.get('subject')}")
                            fetcher.mark_as_processed(msg_id)
                            if item["type"] == "pdf" and os.path.exists(item["path"]):
                                parser.cleanup(item["path"])
                            continue
                        
                        amount, category = None, None
                        if item["type"] == "pdf":
                            amount, category = parser.extract_data(item["path"])
                        elif item["type"] == "text":
                            amount, category = parser.extract_data_from_text(item["content"])
                            
                        if amount is not None and category is not None:
                            # Amount Cache Deduplication (4 days = 345600 seconds)
                            now = time.time()
                            cache_file = "amount_cache.json"
                            amount_cache = {}
                            if os.path.exists(cache_file):
                                try:
                                    with open(cache_file, "r") as f:
                                        amount_cache = json.load(f)
                                except Exception:
                                    pass
                                    
                            amount_cache = {k: v for k, v in amount_cache.items() if now - v < 345600}
                            amount_key = str(amount)
                            
                            if amount_key in amount_cache:
                                silent_log(f"Duplicate amount detected: {amount} €. Skipping.")
                                fetcher.mark_as_processed(msg_id)
                                if item["type"] == "pdf" and os.path.exists(item["path"]):
                                    parser.cleanup(item["path"])
                                continue
                                
                            amount_cache[amount_key] = now
                            with open(cache_file, "w") as f:
                                json.dump(amount_cache, f)
                                
                            success = ha_client.add_to_total(amount, category)
                            if success:
                                fetcher.mark_as_processed(msg_id)
                                # Push notification
                                ha_client.send_notification(f"Neue {category.capitalize()} Rechnung erfasst: {amount} €", title="GroceryParser")
                                
                                # Paperless upload
                                sender = item.get("sender", "Unbekannt")
                                created_date = item.get("created_date")
                                title = item.get("subject", "Unbekanntes Dokument").strip()
                                if len(title) > 80:
                                    title = title[:77] + "..."
                                title = f"{title} ({amount} €)"
                                
                                correspondent_id = paperless_client.get_or_create_correspondent(sender)
                                
                                if item["type"] == "pdf":
                                    paperless_client.upload_document(item["path"], title, category, correspondent_id, created_date)
                                elif item["type"] == "text":
                                    silent_log(f"Skipping Paperless upload (no PDF): {title}")
                        
                        # Cleanup after processing
                        if item["type"] == "pdf" and os.path.exists(item["path"]):
                            parser.cleanup(item["path"])
                    except Exception as inner_e:
                        silent_log(f"Error processing item {item.get('msg_id', 'Unknown')}: {inner_e}")
            silent_log("Check complete.")
        except Exception as e:
            silent_log(f"Error fetching emails: {e}")
            
        if not daemon:
            break
            
        silent_log("Sleeping for 1 hour...")
        time.sleep(3600)

if __name__ == "__main__":
    try:
        config = load_config()
        if "--daemon" in sys.argv:
            run_silent(config, daemon=True)
        else:
            run_silent(config, daemon=False)
    except Exception as e:
        with open("error.log", "w") as f:
            f.write(traceback.format_exc())
        
        # Only show GUI error if we are not running as a daemon
        if "--daemon" not in sys.argv:
            try:
                import tkinter.messagebox as mb
                import tkinter as tk
                root = tk.Tk()
                root.withdraw()
                mb.showerror("Kritischer Fehler", f"Ein Fehler ist aufgetreten:\n{e}\n\nDetails in error.log")
            except:
                pass


