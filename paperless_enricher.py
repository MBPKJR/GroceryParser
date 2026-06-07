#!/usr/bin/env python3
"""
paperless_enricher.py  –  Intelligenter Metadaten-Enricher für Paperless-ngx v2.x

Features:
  - Lernt aus manuellen Änderungen (modified-Timestamp Vergleich)
  - Nutzt den bereits vorhandenen OCR-Text direkt aus der API (kein PDF-Download nötig)
  - Setzt Korrespondent, Dokumententyp, Tags, Datum, Titel, Storage-Path, Custom Fields
  - Unbekannte Firmen → Header-Extraktion → wird in learned_companies.json gespeichert
  - Erkennt manuelle Korrekturen im UI und merkt sich das neue Mapping
  - Drosselt Anfragen damit Paperless nicht überlastet wird
"""

import os, sys, re, time, json, tempfile
import requests
import socket
from pypdf import PdfReader

# Set global socket timeout
socket.setdefaulttimeout(30)

# ── Konfiguration ──────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE   = os.path.join(BASE_DIR, 'config.json')
LOG_FILE      = os.path.join(BASE_DIR, 'enricher.log')
STATE_FILE    = os.path.join(BASE_DIR, 'enricher_state.json')   # {doc_id: {modified, correspondent}}
LEARN_FILE    = os.path.join(BASE_DIR, 'learned_companies.json')

PAGE_SIZE     = 25
PATCH_DELAY   = 2    # s nach PATCH
DOWNLOAD_DELAY= 1    # s nach PDF-Download (Fallback, selten nötig)

# ── Bekannte Firmen-Patterns ───────────────────────────────────────────────────
COMPANY_PATTERNS = [
    (r'\brewe\b',                  'REWE'),
    (r'\bedeka\b',                 'EDEKA'),
    (r'\baldi\b',                  'ALDI'),
    (r'\blidl\b',                  'Lidl'),
    (r'\bkaufland\b',              'Kaufland'),
    (r'\bpenny\b',                 'Penny'),
    (r'\bnetto\b',                 'Netto'),
    (r'\bnorma\b',                 'Norma'),
    (r'\balnatura\b',              'Alnatura'),
    (r'\bdenns\b',                 "Denn's Biomarkt"),
    (r'\bm[uü]ller\b.*drog',       'Müller Drogerie'),
    (r'\brossmann\b',              'Rossmann'),
    (r'\bdm[\s\-]?drogerie\b|dm markt', 'dm Drogerie'),
    (r'\bamazon\b',                'Amazon'),
    (r'\bebay\b',                  'eBay'),
    (r'\bzalando\b',               'Zalando'),
    (r'\botto\b',                  'Otto'),
    (r'\bmediamarkt\b|media markt','MediaMarkt'),
    (r'\bsaturn\b',                'Saturn'),
    (r'\bnotebooksbilliger\b',     'Notebooksbilliger'),
    (r'\blieferando\b',            'Lieferando'),
    (r'\bpaypal\b',                'PayPal'),
    (r'\bklarna\b',                'Klarna'),
    (r'\bstripe\b',                'Stripe'),
    (r'\btelekom\b',               'Telekom'),
    (r'\bvodafone\b',              'Vodafone'),
    (r'\bo2\b',                    'O2'),
    (r'\b1&1\b',                   '1&1'),
    (r'\bapple\b',                 'Apple'),
    (r'\bgoogle\b',                'Google'),
    (r'\bmicrosoft\b',             'Microsoft'),
    (r'\bnetflix\b',               'Netflix'),
    (r'\bspotify\b',               'Spotify'),
    (r'\bhello[\s]?fresh\b',       'HelloFresh'),
    (r'\bflaschenpost\b',          'Flaschenpost'),
    (r'\bgorillas\b',              'Gorillas'),
    (r'\bflink\b',                 'Flink'),
    (r'\bgetir\b',                 'Getir'),
    (r'\bdhl\b',                   'DHL'),
    (r'\bhermes\b',                'Hermes'),
    (r'\bdpd\b',                   'DPD'),
    (r'\bups\b',                   'UPS'),
]

GROCERY_KEYWORDS = [
    'rewe', 'edeka', 'aldi', 'lidl', 'kaufland', 'penny', 'netto', 'norma',
    'alnatura', 'denns', 'rossmann', 'dm drogerie', 'dm-drogerie', 'dm markt',
    'hellofresh', 'hello fresh', 'flaschenpost', 'gorillas', 'flink', 'getir'
]

AMOUNT_RE = re.compile(
    r'(?:Summe|Gesamtbetrag|zu zahlen|Betrag|Rechnungsbetrag|Total|Endbetrag|'
    r'Gesamt|Brutto|Gesamtsumme|Grand Total).{0,80}?'
    r'(?<!\d)([\d]+[.,]\d{2})(?!\d)\s*(?:€|EUR|Euro)',
    re.IGNORECASE | re.DOTALL
)

DATE_RE = re.compile(
    r'(?:Rechnungsdatum|Datum|Bestelldatum|Invoice Date|Date|Belegdatum)'
    r'[:\s]*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})',
    re.IGNORECASE
)

SKIP_HEADER_RE = re.compile(
    r'^(\d{1,5}[\s./\-]|https?://|www\.|rechnung|invoice|bestellung|quittung|'
    r'datum|seite|page|sehr geehrte|vielen dank|ihre bestellung)',
    re.IGNORECASE
)

# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

# ── State: speichert {doc_id: {modified, correspondent_id}} ───────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ── Lern-Datenbank ─────────────────────────────────────────────────────────────
def load_learned():
    if os.path.exists(LEARN_FILE):
        try:
            with open(LEARN_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_learned(db):
    with open(LEARN_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

IGNORED_WORDS = {
    # German salutations & forms
    'herr', 'herrn', 'frau', 'dr', 'prof', 'dipl', 'ing',
    # German articles & prepositions
    'der', 'die', 'das', 'dem', 'den', 'des', 'ein', 'eine', 'einer', 'eines', 'einem', 'einen',
    'in', 'im', 'an', 'am', 'von', 'vom', 'zu', 'zur', 'zum', 'mit', 'für', 'fur', 'und', 'oder',
    # English articles & prepositions
    'the', 'a', 'an', 'of', 'to', 'for', 'with', 'on', 'at', 'by', 'and', 'or', 'in',
    # User's own name & common first names (to avoid learning them as companies)
    'marius', 'koerbes', 'körbes', 'peter', 'jürgen', 'juergen', 'christian', 'thomas', 'michael'
}

GENERIC_WORDS = {
    'finanzamt', 'stadt', 'kanzlei', 'fahrschule', 'apotheke', 'arzt', 'praxis', 'amt', 'rechnung', 
    'bestellung', 'original', 'durchschrift', 'lieferadresse', 'vertrag', 'service', 'kunden', 
    'kundenservice', 'online', 'ticket', 'buchung', 'beleg', 'werkseinstellungen', 'bitlocker', 
    'testzentrum', 'meldebescheinigung', 'unbefristeter', 'buchungsnummer', 'kennnummerder', 'mandant'
}

def clean_company_name(name):
    # Remove common suffixes
    name = re.sub(r'\b(gmbh|ag|e\.v\.|co\.|kg|ltd|inc|unternehmensgruppe\*?)\b', '', name, flags=re.IGNORECASE)
    # Split and clean words
    raw_words = [w.strip(' \t\n\r"\'.,:;()[]{}*&-') for w in name.lower().split()]
    raw_words = [w for w in raw_words if w]
    
    # OCR garbage check: if too many single letter words, ignore it
    single_letters = sum(1 for w in raw_words if len(w) == 1)
    multi_letters = sum(1 for w in raw_words if len(w) > 1)
    if single_letters >= 3 and single_letters >= multi_letters:
        return []
        
    words = [w for w in raw_words if len(w) > 1]
    return [w for w in words if w not in IGNORED_WORDS]

def learn_company(company_name, learned_db):
    """Speichert Firma als lernbares Regex-Pattern."""
    if not company_name or company_name.strip() in ('Unbekannter Korrespondent', 'Unbekannt'):
        return
        
    words = clean_company_name(company_name)
    if not words:
        return
        
    first_word = words[0]
    if first_word in GENERIC_WORDS:
        if len(words) >= 2:
            pattern = f"\\b{re.escape(words[0])}\\s+{re.escape(words[1])}\\b"
        else:
            return # Too generic for a single-word pattern
    else:
        if len(words) >= 2:
            pattern = f"\\b{re.escape(words[0])}\\s+{re.escape(words[1])}\\b"
        else:
            pattern = f"\\b{re.escape(words[0])}\\b"
            
    if pattern not in learned_db and company_name not in learned_db.values():
        learned_db[pattern] = company_name
        save_learned(learned_db)
        log(f"    💡 Gelernt: '{pattern}' → '{company_name}'")

# ── Text-Analyse ───────────────────────────────────────────────────────────────
def detect_company(text, learned_db):
    text_lower = text.lower()
    # 1. Gelernte Patterns (höchste Priorität)
    for pattern, name in learned_db.items():
        try:
            if re.search(pattern, text_lower):
                return name
        except re.error:
            pass
    # 2. Hardcoded Patterns
    for pattern, name in COMPANY_PATTERNS:
        if re.search(pattern, text_lower):
            return name
    return None

def extract_header_name(text):
    """Heuristik: Firmennamen aus den ersten Zeilen extrahieren."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines[:12]:
        if len(line) < 3 or len(line) > 60:
            continue
        if SKIP_HEADER_RE.match(line):
            continue
        if not any(c.isupper() for c in line):
            continue
        if sum(c.isdigit() for c in line) > len(line) * 0.4:
            continue
        return line
    return None

def detect_category(text):
    tl = text.lower()
    return 'lebensmittel' if any(kw in tl for kw in GROCERY_KEYWORDS) else 'sonstiges'

def detect_date(text):
    m = DATE_RE.search(text)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        if len(year) == 2:
            year = '20' + year
        try:
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        except Exception:
            pass
    return None

def detect_amount(text):
    matches = AMOUNT_RE.findall(text)
    if matches:
        try:
            return float(matches[-1].replace(',', '.'))
        except Exception:
            pass
    return None

def pdf_text_from_path(path):
    try:
        reader = PdfReader(path)
        return ' '.join(p.extract_text() or '' for p in reader.pages)
    except Exception as e:
        log(f"    PDF-Lesefehler: {e}")
        return ''

# ── Paperless API ──────────────────────────────────────────────────────────────
class PaperlessAPI:
    def __init__(self, url, token):
        self.base = url.rstrip('/')
        self.h = {
            'Authorization': f'Token {token}',
            'Accept': 'application/json',
        }

    def get(self, path, params=None):
        r = requests.get(f'{self.base}{path}', headers=self.h, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def post(self, path, data):
        r = requests.post(f'{self.base}{path}', headers=self.h, json=data, timeout=15)
        return r

    def patch(self, path, data):
        r = requests.patch(
            f'{self.base}{path}',
            headers={**self.h, 'Content-Type': 'application/json'},
            json=data, timeout=15
        )
        return r.status_code in (200, 201, 204)

    def download_pdf(self, doc_id, dest):
        r = requests.get(
            f'{self.base}/api/documents/{doc_id}/download/',
            headers=self.h, timeout=60, stream=True
        )
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

    # ── Correspondent ──────────────────────────────────────────────────────────
    def get_all_correspondents(self):
        """Gibt {id: name} zurück."""
        result = {}
        page = 1
        while True:
            data = self.get('/api/correspondents/', {'page': page, 'page_size': 100})
            for c in data.get('results', []):
                result[c['id']] = c['name']
            if not data.get('next'):
                break
            page += 1
        return result

    def get_or_create_correspondent(self, name):
        data = self.get('/api/correspondents/', {'name__iexact': name})
        if data.get('count', 0) > 0:
            return data['results'][0]['id']
        r = self.post('/api/correspondents/', {'name': name})
        if r.status_code in (200, 201):
            return r.json().get('id')
        return None

    # ── Document Type ──────────────────────────────────────────────────────────
    def get_or_create_document_type(self, name):
        data = self.get('/api/document_types/', {'name__iexact': name})
        if data.get('count', 0) > 0:
            return data['results'][0]['id']
        r = self.post('/api/document_types/', {'name': name})
        if r.status_code in (200, 201):
            return r.json().get('id')
        return None

    # ── Tags ───────────────────────────────────────────────────────────────────
    def get_or_create_tag(self, name):
        data = self.get('/api/tags/', {'name__iexact': name})
        if data.get('count', 0) > 0:
            return data['results'][0]['id']
        r = self.post('/api/tags/', {'name': name, 'matching_algorithm': 3})
        if r.status_code in (200, 201):
            return r.json().get('id')
        return None

    # ── Documents ──────────────────────────────────────────────────────────────
    def get_documents(self, page=1):
        return self.get('/api/documents/', {'page': page, 'page_size': PAGE_SIZE})

    def patch_document(self, doc_id, payload):
        return self.patch(f'/api/documents/{doc_id}/', payload)

    def upload_document(self, file_path, title, tag_ids=None):
        endpoint = f"{self.base}/api/documents/post_document/"
        data = {"title": title}
        if tag_ids:
            data["tags"] = tag_ids
            
        try:
            with open(file_path, "rb") as f:
                files = {"document": (os.path.basename(file_path), f)}
                r = requests.post(endpoint, headers=self.h, data=data, files=files, timeout=30)
            return r.status_code in (200, 201, 202)
        except Exception as e:
            log(f"Fehler beim Hochladen des Dokuments: {e}")
            return False


# ── Feedback-Loop: manuelle Korrekturen erkennen ───────────────────────────────
def check_for_manual_corrections(api, state, learned_db, correspondent_map):
    """
    Vergleicht den gespeicherten Korrespondenten mit dem aktuellen in Paperless.
    Wenn der User es geändert hat (modified > gespeicherter Zeitstempel),
    wird das neue Mapping gelernt.
    """
    corrections = 0
    for doc_id_str, saved in list(state.items()):
        saved_corr_id = saved.get('correspondent')
        saved_modified = saved.get('modified', '')
        if not saved_corr_id or not saved_modified:
            continue
        try:
            doc = api.get(f'/api/documents/{doc_id_str}/')
            current_modified = doc.get('modified', '')
            current_corr_id  = doc.get('correspondent')

            # Geändert seitdem wir es gesetzt haben?
            if current_modified > saved_modified and current_corr_id != saved_corr_id:
                new_name = correspondent_map.get(current_corr_id)
                if new_name:
                    # Lerne aus der manuellen Korrektur: den PDF-Text neu analysieren
                    content = doc.get('content', '')
                    if content:
                        learn_company(new_name, learned_db)
                        log(f"  📚 Manuelle Korrektur erkannt für Doc {doc_id_str}: "
                            f"'{correspondent_map.get(saved_corr_id, '?')}' → '{new_name}'")
                        corrections += 1
                    # Update state
                    state[doc_id_str]['correspondent'] = current_corr_id
                    state[doc_id_str]['modified'] = current_modified
        except Exception:
            pass
    if corrections:
        log(f"  → {corrections} manuelle Korrekturen gelernt.")
    return corrections


# ── Monatlicher Haushaltsbericht & Duplikaterkennung ───────────────────────────
def check_and_generate_monthly_report(api, state, correspondent_map):
    """
    Prüft, ob für den vergangenen Monat bereits ein Bericht erstellt wurde.
    Falls nicht, wird er generiert und hochgeladen.
    """
    now = time.localtime()
    curr_year = now.tm_year
    curr_month = now.tm_mon
    
    # Berechne vergangenen Monat
    if curr_month == 1:
        prev_year = curr_year - 1
        prev_month = 12
    else:
        prev_year = curr_year
        prev_month = curr_month - 1
        
    prev_month_str = f"{prev_year}-{str(prev_month).zfill(2)}"
    
    # Bereits generiert?
    if state.get("last_report_month") == prev_month_str:
        return
        
    log(f"\n[Haushaltsbericht] Generiere Bericht für {prev_month_str}...")
    
    # Dokumente holen
    docs = []
    page = 1
    while True:
        try:
            data = api.get('/api/documents/', {'page': page, 'page_size': 100})
            docs.extend(data.get('results', []))
            if not data.get('next'):
                break
            page += 1
        except Exception as e:
            log(f"Fehler beim Laden der Dokumente für Haushaltsbericht: {e}")
            return
            
    report_rows = []
    total_amount = 0.0
    category_totals = {}
    
    for doc in docs:
        created = doc.get('created', '')
        if not created or not created.startswith(prev_month_str):
            continue
            
        title = doc.get('title', '')
        corr_id = doc.get('correspondent')
        corr_name = correspondent_map.get(corr_id, "Unbekannt") if corr_id else "Unbekannt"
        
        # Betrag aus Titel extrahieren
        m = re.search(r'\(([\d.]+)\s*€\)', title)
        amount = float(m.group(1)) if m else 0.0
        
        content = doc.get('content', '') or ''
        category = detect_category(content)
        
        report_rows.append({
            'date': created[:10],
            'correspondent': corr_name,
            'title': title,
            'amount': amount,
            'category': category
        })
        
        total_amount += amount
        category_totals[category] = category_totals.get(category, 0.0) + amount
        
    if not report_rows:
        log(f"Keine Dokumente für den Monat {prev_month_str} gefunden. Überspringe Bericht.")
        state["last_report_month"] = prev_month_str
        return
        
    report_rows.sort(key=lambda x: x['date'])
    
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.csv', mode='w', encoding='utf-8', delete=False) as tmp:
        tmp.write("Datum;Händler;Titel;Betrag in EUR;Kategorie\n")
        for row in report_rows:
            tmp.write(f"{row['date']};{row['correspondent']};{row['title']};{row['amount']:.2f};{row['category']}\n")
        
        tmp.write("\nZUSAMMENFASSUNG\n")
        tmp.write(f"Gesamtbetrag;{total_amount:.2f};EUR\n")
        for cat, total in category_totals.items():
            tmp.write(f"Kategorie: {cat};{total:.2f};EUR\n")
            
        tmp_path = tmp.name
        
    try:
        tag_id = api.get_or_create_tag("Haushaltsbuch")
        tag_ids = [tag_id] if tag_id else None
        report_title = f"Haushaltsbericht {prev_month_str}"
        
        ok = api.upload_document(tmp_path, report_title, tag_ids)
        if ok:
            log(f"✓ Haushaltsbericht '{report_title}' erfolgreich hochgeladen.")
            state["last_report_month"] = prev_month_str
        else:
            log("✗ Fehler beim Hochladen des Haushaltsberichts.")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def deduplicate_documents(api):
    log("\nStarte Duplikaterkennung...")
    docs = []
    page = 1
    while True:
        try:
            data = api.get('/api/documents/', {'page': page, 'page_size': 100})
            docs.extend(data.get('results', []))
            if not data.get('next'):
                break
            page += 1
        except Exception as e:
            log(f"Fehler beim Abrufen der Dokumente für Duplikaterkennung: {e}")
            break
            
    seen = {}
    for doc in docs:
        doc_id = doc['id']
        title = doc.get('title', '')
        created = doc.get('created', '')
        corr = doc.get('correspondent')
        
        if not created or not corr:
            continue
            
        created_date = created[:10]
        
        m = re.search(r'\(([\d.]+)\s*€\)', title)
        if not m:
            continue
        amount = m.group(1)
        
        key = (created_date, corr, amount)
        if key not in seen:
            seen[key] = []
        seen[key].append(doc)
        
    for key, matching_docs in seen.items():
        if len(matching_docs) > 1:
            matching_docs.sort(key=lambda x: x['id'])
            original = matching_docs[0]
            duplicates = matching_docs[1:]
            
            created_date, corr, amount = key
            log(f"  ⚠️ Duplikat(e) gefunden für: Datum={created_date}, Betrag={amount} €, Korrespondent_ID={corr}")
            log(f"    Original: [{original['id']}] {original['title']}")
            
            for dup in duplicates:
                dup_id = dup['id']
                dup_title = dup['title']
                if not dup_title.endswith(" (DUPLIKAT)"):
                    new_title = f"{dup_title} (DUPLIKAT)"
                    log(f"    -> Markiere Duplikat: [{dup_id}] '{dup_title}' -> '{new_title}'")
                    api.patch_document(dup_id, {'title': new_title[:128]})


# ── Haupt-Loop ─────────────────────────────────────────────────────────────────
def run():
    with open(CONFIG_FILE) as f:
        config = json.load(f)

    p = config.get('paperless', {})
    api = PaperlessAPI(p['url'], p['token'])

    state       = load_state()
    learned_db  = load_learned()

    log('=' * 60)
    log('Paperless Metadaten-Enricher v2 gestartet')
    log(f'{len(learned_db)} gelernte Muster | {len(state)} bekannte Dokumente')
    log('=' * 60)

    # Erst: Korrespondenten-Map aufbauen (id → name)
    log('Lade Korrespondenten-Verzeichnis...')
    try:
        correspondent_map = api.get_all_correspondents()
        log(f'{len(correspondent_map)} Korrespondenten bekannt.')
    except Exception as e:
        log(f'Fehler beim Laden der Korrespondenten: {e}')
        correspondent_map = {}

    # Manuelle Korrekturen aus UI erkennen
    log('\nPrüfe auf manuelle UI-Korrekturen...')
    check_for_manual_corrections(api, state, learned_db, correspondent_map)
    
    # Monatlichen Haushaltsbericht erzeugen
    try:
        check_and_generate_monthly_report(api, state, correspondent_map)
    except Exception as e:
        log(f"Fehler bei Haushaltsbericht-Erstellung: {e}")
        
    # Duplikaterkennung ausführen
    try:
        deduplicate_documents(api)
    except Exception as e:
        log(f"Fehler bei Duplikaterkennung: {e}")

    save_state(state)

    # Dokument-Typen sicherstellen
    log('\nErstelle Standard-Dokumententypen...')
    try:
        dt_rechnung   = api.get_or_create_document_type('Rechnung')
        dt_quittung   = api.get_or_create_document_type('Quittung / Kassenbon')
        dt_lieferschein = api.get_or_create_document_type('Lieferschein')
        log(f'Dokumententypen: Rechnung={dt_rechnung}, Quittung={dt_quittung}')
    except Exception as e:
        log(f'Warnung Dokumententypen: {e}')
        dt_rechnung = dt_quittung = dt_lieferschein = None

    # Haupt-Enrichment
    page = 1
    total_docs = None
    enriched = skipped = 0

    while True:
        try:
            result = api.get_documents(page=page)
        except Exception as e:
            log(f'API-Fehler Seite {page}: {e}')
            time.sleep(15)
            continue

        if total_docs is None:
            total_docs = result.get('count', '?')
            log(f'\n{total_docs} Dokumente in Paperless. Starte Enrichment...\n')

        docs = result.get('results', [])
        if not docs:
            break

        log(f'-- Seite {page} ({len(docs)} Dokumente) --')

        for doc in docs:
            doc_id   = str(doc['id'])
            title    = doc.get('title', '')
            modified = doc.get('modified', '')
            content  = doc.get('content', '') or ''   # OCR-Text direkt!

            saved = state.get(doc_id, {})

            # Skip: schon verarbeitet UND nicht geändert
            if saved.get('modified') == modified and saved.get('enriched'):
                skipped += 1
                continue

            log(f'  [{doc_id}] {title[:55]}')

            # Text: zuerst aus 'content' (OCR bereits gemacht), sonst PDF herunterladen
            text = content
            if not text.strip() and doc.get('mime_type') == 'application/pdf':
                log('    Content leer – lade PDF herunter...')
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    api.download_pdf(doc['id'], tmp_path)
                    text = pdf_text_from_path(tmp_path)
                    time.sleep(DOWNLOAD_DELAY)
                except Exception as e:
                    log(f'    Download-Fehler: {e}')
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

            if not text.strip():
                log('    Kein Text – überspringe.')
                state[doc_id] = {'modified': modified, 'enriched': False, 'correspondent': doc.get('correspondent')}
                save_state(state)
                continue

            # ── Metadaten ableiten ─────────────────────────────────────────────
            company  = detect_company(text, learned_db)
            category = detect_category(text)
            doc_date = detect_date(text)
            amount   = detect_amount(text)

            # Unbekannt → Header-Heuristik
            if not company:
                header = extract_header_name(text)
                if header:
                    company = header
                    log(f'    Header-Extraktion (nicht automatisch gelernt): "{company}"')

            corr_name = company or 'Unbekannter Korrespondent'
            corr_id   = api.get_or_create_correspondent(corr_name)

            # Dokumententyp
            doc_type_id = None
            tl = text.lower()
            if 'kassenbon' in tl or 'bon nr' in tl or 'quittung' in tl:
                doc_type_id = dt_quittung
            elif 'lieferschein' in tl:
                doc_type_id = dt_lieferschein
            elif dt_rechnung:
                doc_type_id = dt_rechnung

            # Tags (Untouched as requested by user)

            # Titel
            new_title = title
            if company and amount:
                new_title = f'{company} – {("Kassenbon" if doc_type_id == dt_quittung else "Rechnung")} ({amount:.2f} €)'
            elif amount:
                new_title = f'Unbekannt – Rechnung ({amount:.2f} €)'

            # ── PATCH zusammenbauen ────────────────────────────────────────────
            payload = {}
            if corr_id and doc.get('correspondent') != corr_id:
                payload['correspondent'] = corr_id
            if doc_type_id and doc.get('document_type') != doc_type_id:
                payload['document_type'] = doc_type_id
            # payload['tags'] omitted to keep existing tags untouched
            if new_title != title:
                payload['title'] = new_title[:128]
            if doc_date and not doc.get('created_date'):
                payload['created'] = doc_date

            if payload:
                ok = api.patch_document(doc['id'], payload)
                sym = '✓' if ok else '✗'
                log(f'    {sym} {corr_name} | {category} | {amount} € | {doc_date}')
                enriched += int(ok)
            else:
                log('    – Alles aktuell.')

            # State speichern
            state[doc_id] = {
                'modified':      modified,
                'enriched':      True,
                'correspondent': corr_id,
            }
            save_state(state)
            time.sleep(PATCH_DELAY)

        if not result.get('next'):
            break
        page += 1

    log(f'\n✅ Enricher fertig: {enriched} angereichert | {skipped} übersprungen')
    log(f'   Lern-DB: {len(learned_db)} Muster | Korrespondenten: {len(correspondent_map)}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help='Als Dauerdienst laufen (alle 15 Minuten)')
    args = parser.parse_args()

    if args.daemon:
        log('Starte im Daemon-Modus (Intervall: 15 Minuten)...')
        while True:
            try:
                run()
            except Exception as e:
                log(f'Fehler im Enricher-Lauf: {e}')
            log('Schlafe 15 Minuten...\n')
            time.sleep(900)
    else:
        run()
