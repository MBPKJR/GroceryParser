import imaplib
import email
from email.header import decode_header
import os
import datetime
import shutil

class EmailService:
    def __init__(self, config, logger_callback=print):
        self.config = config
        self.log = logger_callback
        self.processed_file = "processed_rosters.json"
        self.processed_ids = self._load_processed_ids()

    def _load_processed_ids(self):
        import json
        if os.path.exists(self.processed_file):
            try:
                with open(self.processed_file, "r") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_processed_id(self, msg_id):
        import json
        self.processed_ids.add(msg_id)
        with open(self.processed_file, "w") as f:
            json.dump(list(self.processed_ids), f)

    def fetch_latest_roster(self, download_dir):
        if not os.path.exists(download_dir):
            os.makedirs(download_dir)

        roster_files = []
        
        for account in self.config.get("emails", []):
            try:
                mail = imaplib.IMAP4_SSL(account["imap_server"], account.get("imap_port", 993))
                mail.login(account["username"], account["app_password"])
                mail.select("inbox")
                
                # Search for emails from the last 30 days
                date = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%d-%b-%Y")
                status, messages = mail.search(None, f'(SINCE "{date}")')
                
                if status != "OK":
                    continue

                for num in messages[0].split()[::-1]: # Latest first
                    status, msg_data = mail.fetch(num, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            msg_id = msg.get("Message-ID")
                            
                            if msg_id in self.processed_ids:
                                continue

                            subject = self._decode_header(msg.get("Subject", ""))
                            sender = self._decode_header(msg.get("From", ""))
                            self.log(f"Prüfe Email von {sender}: {subject}")

                            # Check if sender is allowed
                            allowed_senders = self.config.get("allowed_senders", [])
                            is_allowed = False
                            for allowed in allowed_senders:
                                if allowed.lower() in sender.lower():
                                    is_allowed = True
                                    break
                            
                            if not is_allowed and allowed_senders:
                                continue

                            # Check if subject contains "Dienstplan" or similar
                            if "dienstplan" in subject.lower():

                                for part in msg.walk():
                                    if part.get_content_maintype() == 'multipart':
                                        continue
                                    if part.get('Content-Disposition') is None:
                                        continue
                                    
                                    filename = self._decode_header(part.get_filename())
                                    if filename and (filename.lower().endswith('.xls') or filename.lower().endswith('.xlsx')):
                                        filepath = os.path.join(download_dir, filename)
                                        with open(filepath, 'wb') as f:
                                            f.write(part.get_payload(decode=True))
                                        
                                        self.log(f"Dienstplan heruntergeladen: {filename}")
                                        roster_files.append(filepath)
                                        self._save_processed_id(msg_id)
                                        # Usually we only need the latest one per check
                                        break
                
                mail.logout()
            except Exception as e:
                self.log(f"Fehler beim Email-Abruf: {e}")
        
        return roster_files

    def _decode_header(self, header):
        if not header:
            return ""
        decoded_parts = decode_header(header)
        result = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(encoding or "utf-8", errors="ignore")
            else:
                result += str(part)
        return result
