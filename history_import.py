#!/usr/bin/env python3
"""
history_import.py – Historischer Paperless-Import
Lädt ausschließlich PDFs (Rechnungen) aus allen konfigurierten
E-Mail-Konten und schiebt sie gedrosselt in Paperless-ngx.
Home Assistant wird dabei komplett ignoriert.
"""
import os
import sys
import imaplib
import email
import time
import json
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime

from paperless import PaperlessClient
from parser import ReceiptParser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
LOG_FILE = os.path.join(BASE_DIR, 'history_import.log')
HISTORY_FILE = os.path.join(BASE_DIR, 'history_processed.json')
CHUNK_SIZE = 30       # E-Mails pro Batch
UPLOAD_DELAY = 6      # Sekunden Pause nach jedem Paperless-Upload


def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def silent_log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_history(ids):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(list(ids), f)


def decode_str(value):
    if not value:
        return ""
    result = ""
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            result += part.decode(enc or 'utf-8', errors='ignore')
        else:
            result += str(part)
    return result


def extract_sender_name(from_header):
    decoded = decode_str(from_header)
    match = re.match(r'^(.*?)\s*<', decoded)
    if match:
        name = match.group(1).strip(' \t\n\r"\'')
    else:
        name = decoded.strip()

    if '@' in name or not name:
        domain_match = re.search(r'@([\w.]+)', decoded)
        if domain_match:
            parts = domain_match.group(1).split('.')
            name = parts[-2].capitalize() if len(parts) >= 2 else parts[0].capitalize()
        else:
            name = "Unbekannt"
    return name


BLACKLIST = [
    "versand", "versendet", "shipped", "shipment",
    "bestellbestätigung", "bestellbestatigung",
    "order confirmation", "order confirmed",
    "deine bestellung ist", "your order has been"
]


import socket

# Set global socket timeout to prevent indefinite hanging
socket.setdefaulttimeout(30)

def run():
    silent_log("=" * 50)
    silent_log("Historischer Paperless-Import gestartet")
    silent_log("=" * 50)

    config = load_config()
    paperless = PaperlessClient(config)
    parser = ReceiptParser()
    history_ids = load_history()
    session_ids = set()

    accounts = config.get('emails', [])
    silent_log(f"{len(accounts)} E-Mail-Konten geladen.")

    for account in accounts:
        user = account.get('username', account.get('user', ''))
        password = account.get('app_password', account.get('password', ''))
        server = account.get('imap_server', 'imap.gmail.com')
        port = int(account.get('imap_port', 993))
        folder = account.get('folder', 'INBOX')

        silent_log(f"\nKonto: {user}")

        def connect_imap():
            try:
                m = imaplib.IMAP4_SSL(server, port, timeout=30)
                m.login(user, password)
                m.select(folder)
                return m
            except Exception as e:
                silent_log(f"  Verbindungsfehler: {e}")
                return None

        mail = connect_imap()
        if not mail:
            continue

        try:
            status, data = mail.search(None, 'ALL')
            if status != 'OK' or not data[0]:
                silent_log("  Keine E-Mails gefunden.")
                mail.logout()
                continue
        except Exception as e:
            silent_log(f"  Fehler bei Suche: {e}")
            continue

        all_nums = data[0].split()
        total = len(all_nums)
        silent_log(f"  {total} E-Mails gefunden. Starte Verarbeitung in {CHUNK_SIZE}er-Blöcken...")

        uploaded = 0
        skipped = 0

        for i in range(0, total, CHUNK_SIZE):
            chunk = all_nums[i:i + CHUNK_SIZE]
            silent_log(f"  Block {i // CHUNK_SIZE + 1}/{(total + CHUNK_SIZE - 1) // CHUNK_SIZE} ({len(chunk)} Mails)...")

            for num in chunk:
                try:
                    try:
                        status, msg_data = mail.fetch(num, '(RFC822)')
                    except (socket.error, imaplib.IMAP4.error, EOFError) as e:
                        silent_log(f"    Verbindung verloren ({e}). Versuche Reconnect...")
                        mail = connect_imap()
                        if not mail:
                            silent_log("    Reconnect fehlgeschlagen. Beende diesen Durchlauf.")
                            sys.exit(1) # Let systemd restart
                        status, msg_data = mail.fetch(num, '(RFC822)')

                    if status != 'OK':
                        continue

                    for part in msg_data:
                        if not isinstance(part, tuple):
                            continue

                        msg = email.message_from_bytes(part[1])
                        msg_id = msg.get('Message-ID', '')

                        if not msg_id or msg_id in history_ids or msg_id in session_ids:
                            continue
                        session_ids.add(msg_id)

                        subject = decode_str(msg.get('Subject', ''))
                        subject_lc = subject.lower()

                        # Nur Eingangsrechnungen
                        if any(b in subject_lc for b in BLACKLIST):
                            history_ids.add(msg_id)
                            skipped += 1
                            continue

                        # Datum
                        created_date = None
                        try:
                            dt = parsedate_to_datetime(msg.get('Date', ''))
                            created_date = dt.isoformat()
                        except Exception:
                            pass

                        sender_name = extract_sender_name(msg.get('From', ''))

                        # PDFs suchen
                        for p in msg.walk():
                            disposition = str(p.get('Content-Disposition', ''))
                            filename = p.get_filename()
                            if filename and filename.lower().endswith('.pdf'):
                                safe_name = ''.join(c for c in f"hist_{msg_id.strip('<>')}.pdf" if c.isalnum() or c in '._-')
                                filepath = f'/tmp/{safe_name}'

                                try:
                                    with open(filepath, 'wb') as pdf_f:
                                        pdf_f.write(p.get_payload(decode=True))
                                except Exception as e:
                                    silent_log(f"    PDF-Schreibfehler: {e}")
                                    continue

                                amount, category = parser.extract_data(filepath)

                                if amount is not None:
                                    title = subject.strip()
                                    if len(title) > 80:
                                        title = title[:77] + '...'
                                    title = f"{title} ({amount} €)"

                                    correspondent_id = paperless.get_or_create_correspondent(sender_name)
                                    success = paperless.upload_document(filepath, title, category, correspondent_id, created_date)

                                    if success:
                                        silent_log(f"    ✓ Hochgeladen: {title}")
                                        uploaded += 1
                                        history_ids.add(msg_id)
                                        save_history(history_ids)
                                        time.sleep(UPLOAD_DELAY)
                                    else:
                                        silent_log(f"    ✗ Upload fehlgeschlagen: {title}")
                                else:
                                    # Kein Betrag in PDF → trotzdem als verarbeitet markieren
                                    history_ids.add(msg_id)
                                    save_history(history_ids)

                                if os.path.exists(filepath):
                                    os.remove(filepath)

                        history_ids.add(msg_id)

                except Exception as e:
                    silent_log(f"    Fehler bei Nachricht {num}: {e}")

            # Nach jedem Block kurz speichern
            save_history(history_ids)

        try:
            mail.logout()
        except Exception:
            pass

        silent_log(f"\n  Konto {user} fertig: {uploaded} hochgeladen, {skipped} übersprungen.")

    silent_log("\nHistorischer Import abgeschlossen!")


if __name__ == '__main__':
    run()
