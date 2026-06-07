import requests
import json

class HomeAssistantClient:
    def __init__(self, config):
        self.url = config["home_assistant"]["url"].rstrip("/")
        self.token = config["home_assistant"]["token"]
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _get_entity_id(self, category):
        if category == "lebensmittel":
            return "sensor.lebensmittel_ausgaben_monat"
        else:
            return "sensor.sonstige_ausgaben_monat"

    def get_current_total(self, category):
        """Fetches the current state of the entity."""
        entity_id = self._get_entity_id(category)
        endpoint = f"{self.url}/api/states/{entity_id}"
        try:
            response = requests.get(endpoint, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data.get("state", 0.0))
        except Exception as e:
            print(f"Error getting state from Home Assistant: {e}")
            return 0.0

    def add_to_total(self, amount, category):
        """Adds the amount to the current total and updates HA."""
        current = self.get_current_total(category)
        new_total = current + amount
        
        entity_id = self._get_entity_id(category)
        friendly_name = "Lebensmittel Ausgaben diesen Monat" if category == "lebensmittel" else "Sonstige Ausgaben diesen Monat"
        
        endpoint = f"{self.url}/api/states/{entity_id}"
        payload = {
            "state": round(new_total, 2),
            "attributes": {
                "unit_of_measurement": "€",
                "friendly_name": friendly_name,
                "icon": "mdi:cart" if category == "lebensmittel" else "mdi:receipt",
                "device_class": "monetary",
                "state_class": "total_increasing"
            }
        }
        
        try:
            response = requests.post(endpoint, headers=self.headers, json=payload, timeout=10)
            response.raise_for_status()
            print(f"Successfully updated {category} in Home Assistant. New total: {new_total} €")
            return True
        except Exception as e:
            print(f"Error updating Home Assistant: {e}")
            return False

    def send_notification(self, message, title="GroceryParser"):
        """Sends a push notification via Home Assistant."""
        endpoint = f"{self.url}/api/services/notify/notify"
        payload = {
            "title": title,
            "message": message
        }
        try:
            response = requests.post(endpoint, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"[HomeAssistant] Notification sent: {message}")
            else:
                print(f"[HomeAssistant] Failed to send notification: {response.status_code}")
        except Exception as e:
            print(f"[HomeAssistant] Exception sending notification: {e}")

    def update_health(self, state="healthy", attributes=None):
        """Updates the groceryparser health sensor in Home Assistant."""
        entity_id = "sensor.groceryparser_health"
        endpoint = f"{self.url}/api/states/{entity_id}"
        
        payload = {
            "state": state,
            "attributes": {
                "friendly_name": "GroceryParser System Health",
                "icon": "mdi:heart-pulse",
                **(attributes or {})
            }
        }
        try:
            response = requests.post(endpoint, headers=self.headers, json=payload, timeout=10)
            response.raise_for_status()
            print(f"[HomeAssistant] Health status updated: {state}")
            return True
        except Exception as e:
            print(f"[HomeAssistant] Error updating health status: {e}")
            return False

