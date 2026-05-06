import imaplib
import email
from email.header import decode_header
import json
import os
import datetime

class EmailFetcher:
    def __init__(self, config, logger_callback=print):
        self.emails_config = config.get("emails", [])
        self.processed_file = "processed_emails.json"
        self.processed_ids = self._load_processed_ids()
        self.log = logger_callback
        
    def _load_processed_ids(self):
        if os.path.exists(self.processed_file):
            try:
                with open(self.processed_file, "r") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_processed_id(self, msg_id):
        self.processed_ids.add(msg_id)
        with open(self.processed_file, "w") as f:
            json.dump(list(self.processed_ids), f)

    def connect(self, account):
        try:
            self.mail = imaplib.IMAP4_SSL(account["imap_server"], account["imap_port"])
            self.mail.login(account["username"], account["app_password"])
            self.mail.select("inbox")
            return True
        except Exception as e:
            self.log(f"Login fehlgeschlagen für {account['username']}: {e}")
            return False

    def disconnect(self):
        if hasattr(self, 'mail'):
            try:
                self.mail.close()
                self.mail.logout()
            except:
                pass

    def _get_current_month_search_criterion(self):
        today = datetime.datetime.now()
        first_day = today.replace(day=1)
        return first_day.strftime("%d-%b-%Y")

    def fetch_new_receipts(self):
        items = []
        since_date = self._get_current_month_search_criterion()
        session_fetched_ids = set()
        try:
            for account in self.emails_config:
                self.log(f"Verbinde zu E-Mail-Konto: {account['username']}")
                if not self.connect(account):
                    continue
                    
                status, messages = self.mail.search(None, f'(SINCE "{since_date}")')
                
                if status != "OK":
                    continue

                for num in messages[0].split():
                    status, msg_data = self.mail.fetch(num, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            msg_id = msg.get("Message-ID")
                            
                            subject_header = msg.get("Subject", "")
                            decoded_subject = ""
                            for part, enc in decode_header(subject_header):
                                if isinstance(part, bytes):
                                    decoded_subject += part.decode(enc or "utf-8", errors="ignore")
                                else:
                                    decoded_subject += str(part)
                                    
                            from_header = msg.get("From", "")
                            decoded_from = ""
                            for part, enc in decode_header(from_header):
                                if isinstance(part, bytes):
                                    decoded_from += part.decode(enc or "utf-8", errors="ignore")
                                else:
                                    decoded_from += str(part)
                                    
                            date_header = msg.get("Date")
                            created_date = None
                            if date_header:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    dt = parsedate_to_datetime(date_header)
                                    created_date = dt.isoformat()
                                except Exception:
                                    pass
                                    
                            import re
                            match = re.match(r'^(.*?)\s*<', decoded_from)
                            if match:
                                sender_name = match.group(1).strip(' \t\n\r"\'')
                            else:
                                sender_name = decoded_from.strip(' \t\n\r"\'')
                                
                            if '@' in sender_name:
                                domain = sender_name.split('@')[-1].split('.')[0].capitalize()
                                sender_name = domain
                                
                            if not sender_name:
                                sender_name = "Unbekannt"
                                    
                            if msg_id in self.processed_ids or msg_id in session_fetched_ids:
                                continue
                                
                            session_fetched_ids.add(msg_id)
                                
                            has_pdf = False
                            body_text = ""
                            body_html = ""
                            
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                content_disposition = str(part.get("Content-Disposition"))
                                
                                # Look for PDF attachments
                                if "attachment" in content_disposition or part.get_filename():
                                    file_name = part.get_filename()
                                    if file_name and file_name.lower().endswith('.pdf'):
                                        safe_name = f"receipt_{msg_id.strip('<>')}.pdf"
                                        safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._-")
                                        filepath = os.path.join(os.getcwd(), safe_name)
                                        
                                        with open(filepath, "wb") as f:
                                            f.write(part.get_payload(decode=True))
                                        
                                        items.append({"type": "pdf", "path": filepath, "msg_id": msg_id, "subject": decoded_subject, "sender": sender_name, "created_date": created_date})
                                        self.log(f"PDF gefunden: {safe_name}")
                                        has_pdf = True
                                else:
                                    # Extract email body if it's not an attachment
                                    if content_type == "text/plain":
                                        try:
                                            body_text = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        except:
                                            pass
                                    elif content_type == "text/html":
                                        try:
                                            body_html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        except:
                                            pass
                            
                            # If no PDF was found, append the email body for parsing
                            if not has_pdf and (body_text or body_html):
                                items.append({"type": "text", "content": body_html if body_html else body_text, "msg_id": msg_id, "subject": decoded_subject, "sender": sender_name, "created_date": created_date})
                                self.log(f"E-Mail-Text extrahiert (Kein PDF): {decoded_subject}")
                
                self.disconnect()
            return items
        except Exception as e:
            self.log(f"Fehler beim Abrufen der E-Mails: {e}")
            self.disconnect()
            return []

    def get_all_message_numbers(self, search_criterion="ALL"):
        if not self.connect():
            return []
        try:
            status, messages = self.mail.search(None, search_criterion)
            if status == "OK" and messages[0]:
                return messages[0].split()
            return []
        except Exception as e:
            self.log(f"Error fetching message numbers: {e}")
            return []
        finally:
            self.disconnect()

    def fetch_specific_numbers(self, numbers, session_fetched_ids=None):
        if session_fetched_ids is None:
            session_fetched_ids = set()
            
        if not self.connect():
            return []
            
        items = []
        try:
            for num in numbers:
                status, msg_data = self.mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                    
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        msg_id = msg.get("Message-ID")
                        
                        subject_header = msg.get("Subject", "")
                        decoded_subject = ""
                        for part, enc in decode_header(subject_header):
                            if isinstance(part, bytes):
                                decoded_subject += part.decode(enc or "utf-8", errors="ignore")
                            else:
                                decoded_subject += str(part)
                                
                        from_header = msg.get("From", "")
                        decoded_from = ""
                        for part, enc in decode_header(from_header):
                            if isinstance(part, bytes):
                                decoded_from += part.decode(enc or "utf-8", errors="ignore")
                            else:
                                decoded_from += str(part)
                                
                        date_header = msg.get("Date")
                        created_date = None
                        if date_header:
                            try:
                                from email.utils import parsedate_to_datetime
                                dt = parsedate_to_datetime(date_header)
                                created_date = dt.isoformat()
                            except Exception:
                                pass
                                
                        import re
                        match = re.match(r'^(.*?)\s*<', decoded_from)
                        if match:
                            sender_name = match.group(1).strip(' \t\n\r"\'')
                        else:
                            sender_name = decoded_from.strip(' \t\n\r"\'')
                            
                        if '@' in sender_name:
                            domain = sender_name.split('@')[-1].split('.')[0].capitalize()
                            sender_name = domain
                            
                        if not sender_name:
                            sender_name = "Unbekannt"
                                
                        if msg_id in self.processed_ids or msg_id in session_fetched_ids:
                            continue
                            
                        session_fetched_ids.add(msg_id)
                            
                        has_pdf = False
                        body_text = ""
                        body_html = ""
                        
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            
                            if "attachment" in content_disposition or part.get_filename():
                                file_name = part.get_filename()
                                if file_name and file_name.lower().endswith('.pdf'):
                                    safe_name = f"receipt_{msg_id.strip('<>')}.pdf"
                                    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._-")
                                    filepath = os.path.join(os.getcwd(), safe_name)
                                    
                                    with open(filepath, "wb") as f:
                                        f.write(part.get_payload(decode=True))
                                    
                                    items.append({"type": "pdf", "path": filepath, "msg_id": msg_id, "subject": decoded_subject, "sender": sender_name, "created_date": created_date})
                                    has_pdf = True
                            else:
                                if content_type == "text/plain":
                                    try:
                                        body_text = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    except:
                                        pass
                                elif content_type == "text/html":
                                    try:
                                        body_html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    except:
                                        pass
                        
                        if not has_pdf and (body_text or body_html):
                            items.append({"type": "text", "content": body_html if body_html else body_text, "msg_id": msg_id, "subject": decoded_subject, "sender": sender_name, "created_date": created_date})
                            
            self.disconnect()
            return items
        except Exception as e:
            self.log(f"Error in fetch_specific_numbers: {e}")
            self.disconnect()
            return []

    def mark_as_processed(self, msg_id):
        self._save_processed_id(msg_id)
