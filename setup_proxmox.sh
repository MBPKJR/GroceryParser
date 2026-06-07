#!/bin/bash

# Farben für die Ausgabe
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}>>> Dienstplan-zu-iCal: Automatisches Setup wird gestartet...${NC}"

# 1. System-Check & Docker-Installation
if ! [ -x "$(command -v docker)" ]; then
  echo "Docker wird nachinstalliert..."
  curl -fsSL https://get.docker.com -o get-docker.sh
  sh get-docker.sh
  rm get-docker.sh
fi

if ! [ -x "$(command -v docker-compose)" ]; then
  echo "Docker-Compose wird nachinstalliert..."
  apt-get update && apt-get install -y docker-compose
fi

# 2. Container bauen und im Hintergrund starten
echo -e "${GREEN}>>> Container wird erstellt und gestartet...${NC}"
docker-compose up -d --build

# 3. IP-Adresse für den finalen Link holen
IP_ADDR=$(hostname -I | awk '{print $1}')

echo -e "\n${GREEN}===================================================="
echo -e "FERTIG! Beide Programme laufen jetzt im Hintergrund."
echo -e "1) Kalender-Link für dein Handy/Outlook:"
echo -e "   http://${IP_ADDR}:8080/calendar.ics"
echo -e ""
echo -e "2) VRR Live-Abfahrtsmonitor (PWA):"
echo -e "   http://${IP_ADDR}:3000"
echo -e "====================================================${NC}"
