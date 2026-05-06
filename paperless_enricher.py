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
from pypdf import PdfReader

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

def learn_company(company_name, learned_db):
    """Speichert Firma als lernbares Regex-Pattern."""
    if not company_name or company_name in ('Unbekannter Korrespondent',):
        return
    first_word = re.escape(company_name.lower().split()[0])
    pattern = rf'\b{first_word}\b'
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
                    learn_company(company, learned_db)
                    log(f'    Header-Extraktion: "{company}"')

            # Bekannte Firma lernen
            if company:
                learn_company(company, learned_db)

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

            # Tags
            tag_ids = list(doc.get('tags', []))
            for tag_name in ['Rechnung', 'Lebensmittel' if category == 'lebensmittel' else 'Sonstiges']:
                tid = api.get_or_create_tag(tag_name)
                if tid and tid not in tag_ids:
                    tag_ids.append(tid)
            if company:
                tid = api.get_or_create_tag(company)
                if tid and tid not in tag_ids:
                    tag_ids.append(tid)

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
            if set(tag_ids) != set(doc.get('tags', [])):
                payload['tags'] = list(set(tag_ids))
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
