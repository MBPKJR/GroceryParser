import requests
import json
import os

class PaperlessClient:
    def __init__(self, config):
        p_config = config.get("paperless", {})
        self.url = p_config.get("url", "").rstrip("/")
        self.token = p_config.get("token", "")
        self.enabled = p_config.get("enabled", False)
        
        self.headers = {
            "Authorization": f"Token {self.token}"
        }

    def get_or_create_correspondent(self, name):
        if not self.enabled or not self.url or not self.token or not name:
            return None
            
        try:
            # First try to find existing
            search_url = f"{self.url}/api/correspondents/"
            params = {"name__iexact": name}
            res = requests.get(search_url, headers=self.headers, params=params, timeout=10)
            
            if res.status_code == 200:
                data = res.json()
                if data.get("count", 0) > 0:
                    return data["results"][0]["id"]
                    
            # If not found, create new
            create_url = f"{self.url}/api/correspondents/"
            create_data = {"name": name}
            res_create = requests.post(create_url, headers=self.headers, json=create_data, timeout=10)
            
            if res_create.status_code in [200, 201]:
                return res_create.json().get("id")
                
        except Exception as e:
            print(f"[Paperless] Error handling correspondent '{name}': {e}")
            
        return None

    def upload_document(self, file_path, title, category, correspondent_id=None, created_date=None):
        if not self.enabled or not self.url or not self.token:
            return False
            
        endpoint = f"{self.url}/api/documents/post_document/"
        
        # We can add tags based on the category if we want
        data = {
            "title": title
        }
        
        if correspondent_id:
            data["correspondent"] = correspondent_id
            
        if created_date:
            data["created"] = created_date
        
        try:
            with open(file_path, "rb") as f:
                files = {
                    "document": (os.path.basename(file_path), f)
                }
                response = requests.post(endpoint, headers=self.headers, data=data, files=files, timeout=30)
                
            if response.status_code in [200, 201, 202]:
                print(f"[Paperless] Uploaded '{title}' successfully.")
                return True
            else:
                print(f"[Paperless] Error uploading '{title}': {response.status_code} {response.text}")
                return False
        except Exception as e:
            print(f"[Paperless] Exception during upload: {e}")
            return False

    def upload_text_document(self, text, title, category, correspondent_id=None, created_date=None):
        # Create a temporary txt file for emails without attachments
        if not self.enabled or not self.url or not self.token:
            return False
            
        temp_path = "temp_receipt.txt"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(text)
            
            res = self.upload_document(temp_path, title, category, correspondent_id, created_date)
            os.remove(temp_path)
            return res
        except Exception as e:
            print(f"[Paperless] Error handling text document: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
