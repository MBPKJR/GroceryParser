import re
import os
from pypdf import PdfReader
from bs4 import BeautifulSoup

class ReceiptParser:
    def __init__(self):
        # Strict regex for total amount
        keywords = r"(?:Summe|Gesamtbetrag|zu zahlen|Betrag|Rechnungsbetrag|Total|Endbetrag)"
        # Matches within 60 chars, REQUIRES EUR or € symbol after the number to avoid ANY false positives.
        self.pattern = re.compile(rf"{keywords}.{{0,60}}?(?<![\d\.,])(\d+[\.,]\d{{2}})(?![\d\.,])\s*(?:€|EUR|Euro)", re.IGNORECASE | re.DOTALL)
        
        # Keywords to identify grocery stores
        self.grocery_keywords = [
            "rewe", "edeka", "aldi", "lidl", "kaufland", "penny", 
            "netto", "norma", "rossmann", "dm-drogerie", "dm drogerie", "alnatura", "denns"
        ]

    def _process_clean_text(self, clean_text):
        """Shared logic to determine category and amount from clean text."""
        text_lower = clean_text.lower()
        category = "sonstiges"
        for kw in self.grocery_keywords:
            if kw in text_lower:
                category = "lebensmittel"
                break
        
        matches = self.pattern.findall(clean_text)
        if matches:
            amount_str = matches[-1].replace(',', '.')
            return float(amount_str), category
        else:
            return None, None

    def extract_data(self, pdf_path):
        """
        Extracts the total amount and categorizes the receipt from a PDF.
        Returns (amount, category) or (None, None).
        """
        try:
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            
            clean_text = re.sub(r'\s+', ' ', text)
            
            amount, category = self._process_clean_text(clean_text)
            if amount is None:
                print(f"Warning: Could not find total in {pdf_path}")
            return amount, category
                
        except Exception as e:
            print(f"Error parsing PDF {pdf_path}: {e}")
            return None, None

    def extract_data_from_text(self, raw_text):
        """
        Extracts the total amount and categorizes the receipt from an HTML or Plain email body.
        Returns (amount, category) or (None, None).
        """
        try:
            # Parse HTML to get clean text
            soup = BeautifulSoup(raw_text, "html.parser")
            text = soup.get_text(separator=' ')
            
            clean_text = re.sub(r'\s+', ' ', text)
            
            amount, category = self._process_clean_text(clean_text)
            if amount is None:
                print(f"Warning: Could not find total in E-Mail Body")
            return amount, category
                
        except Exception as e:
            print(f"Error parsing E-Mail text: {e}")
            return None, None
            
    def cleanup(self, pdf_path):
        """Deletes the PDF after processing"""
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception as e:
            print(f"Error deleting {pdf_path}: {e}")
