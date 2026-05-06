# 🛒 GroceryParser & Paperless-ngx Automator

> Honors to Jonny's idea

Ein vollautomatisches System zur Erfassung von Lebensmittelausgaben und Belegarchivierung. Dieses Projekt überwacht E-Mail-Konten nach Rechnungen, extrahiert Beträge, synchronisiert diese mit **Home Assistant** Sensoren und archiviert die Belege intelligent in **Paperless-ngx**.

## 🚀 Features

- **📩 Real-Time Parsing**: Überwacht IMAP-Postfächer auf neue Mails (PDF-Anhänge & Text-Bodies).
- **📊 Home Assistant Integration**: Aktualisiert Sensoren für "Lebensmittel" und "Sonstiges" in Echtzeit.
- **📁 Paperless-ngx Archivierung**: Lädt Belege automatisch hoch, setzt Korrespondenten, Tags und Rechnungsdaten.
- **🧠 Lernendes System**: Erkennt Firmennamen aus PDF-Headern und lernt aus manuellen Korrekturen in der Paperless-UI.
- **🕒 Historischer Import**: Ein dediziertes Skript, um den gesamten E-Mail-Bestand der letzten Jahre nachträglich in Paperless zu importieren.
- **🛡️ Robustheit**: Läuft als systemd-Service mit automatischer Wiederherstellung nach Abstürzen.

## 🛠️ Installation (LXC / Debian)

### 1. Repository klonen & Venv erstellen
```bash
git clone https://github.com/DEIN-USER/GroceryParser.git /app
cd /app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Konfiguration
Kopiere die Beispiel-Konfiguration und fülle deine Daten aus:
```bash
cp config.json.example config.json
nano config.json
```

### 3. Services einrichten
Um das System dauerhaft im Hintergrund laufen zu lassen:
```bash
# Kopiere die Service-Dateien nach /etc/systemd/system/
sudo cp services/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable groceryparser paperless_enricher
sudo systemctl start groceryparser paperless_enricher
```

## 🐳 Installation (Docker)

Die einfachste Methode für Docker-Nutzer:

1.  **Repository klonen**
2.  **Konfiguration erstellen**: `cp config.json.example config.json` und ausfüllen.
3.  **Starten**:
    ```bash
    docker-compose up -d
    ```

## 🧹 Komponenten

- `main.py`: Der Haupt-Daemon, der auf neue E-Mails wartet.
- `paperless_enricher.py`: Ein Daemon, der regelmäßig Paperless-Dokumente prüft und Metadaten schärft.
- `history_import.py`: Einmaliges Skript für den Import alter Belege.
- `email_client.py`: IMAP-Handling und Attachment-Extraktion.
- `parser.py`: Regex-basierte Extraktion von Beträgen und Kategorien.

## 🎓 Lern-Funktion
Der `paperless_enricher` speichert erkannte Muster in `learned_companies.json`. Wenn du in Paperless einen Korrespondenten manuell änderst, erkennt der Enricher dies beim nächsten Lauf und merkt sich das neue Muster für zukünftige Dokumente automatisch.

## ⚖️ Lizenz

Dieses Projekt ist unter der [MIT License](LICENSE) lizenziert. Du kannst es frei verwenden, kopieren und modifizieren.

---
*Honors to Jonny's idea. Entwickelt für den Einsatz auf Proxmox LXC Containern.*
