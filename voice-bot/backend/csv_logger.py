"""
Thread-safe CSV logger for lead data.
Handles CSV file creation, appending, and updating for lead export functionality.
"""

from __future__ import annotations

import csv
import os
import threading
from datetime import datetime, timezone
from typing import Any

from config import Config


# CSV file path - stored in backend/data folder
CSV_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_FILE_PATH = os.path.join(CSV_DIR, "leads.csv")

# CSV column definitions - Added CallID for tracking and deduplication
CSV_COLUMNS = [
    "Timestamp",
    "Source",
    "Name",
    "Phone",
    "Email",
    "City",
    "Property Interest",
    "Budget",
    "Notes",
    "CallID" # New column for deduplication
]

# Thread lock for safe concurrent writes
_lock = threading.Lock()


def _ensure_csv_exists() -> None:
    """
    Ensure the CSV file exists with proper headers.
    Creates the file and writes headers if it doesn't exist.
    Thread-safe via external lock.
    """
    # Ensure directory exists
    os.makedirs(CSV_DIR, exist_ok=True)
    
    # Check if file exists and has content
    file_exists = os.path.exists(CSV_FILE_PATH)
    has_headers = False
    
    if file_exists:
        try:
            with open(CSV_FILE_PATH, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                first_row = next(reader, None)
                if first_row and "Timestamp" in first_row:
                    # Check if CallID is in headers (for migration)
                    if "CallID" in first_row:
                        has_headers = True
                    else:
                        # File needs migration - we'll handle by rewriting
                        has_headers = False
        except Exception:
            pass
    
    # Create file with headers if needed
    if not file_exists or not has_headers:
        # If it exists but is old, we keep the data and just add headers if possible, 
        # but for simplicity in this version, we start a fresh header-compliant file 
        # or append to existing if column counts match.
        with open(CSV_FILE_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def extract_city_from_interest(interest: str) -> str:
    """
    Extract city name from property interest string.
    """
    if not interest or not isinstance(interest, str):
        return ""
    # Common cities in Indian real estate listings
    cities = ["delhi", "new delhi", "noida", "gurgaon", "mumbai", "bangalore", "bhubaneswar", "kochi", "lucknow", "indore", "pune", "chennai", "kolkata", "hyderabad", "odisha", "balasore"]
    interest_lower = interest.lower()
    for city in cities:
        if city in interest_lower:
            return city.strip().title()
    return ""


def log_lead_to_csv(lead_data: dict[str, Any], source: str = "unknown") -> bool:
    """
    Log a lead to the CSV file. If a lead with the same CallID or Phone (recent)
    exists, updates that row instead of appending.
    
    Args:
        lead_data: Dictionary containing lead information
        source: Lead source ("voice" or "whatsapp")
    
    Returns:
        True if successful, False otherwise
    """
    with _lock:
        try:
            # Ensure file exists with headers
            _ensure_csv_exists()
            
            # Extract identifiers
            call_id = str(lead_data.get("call_id", lead_data.get("callSid", lead_data.get("call_sid", ""))))
            phone = str(lead_data.get("phone", lead_data.get("phoneNumber", ""))).strip()
            
            # Excel formatting: always ensure phone number has a leading "+" to prevent scientific notation formatting
            if phone:
                if phone.isdigit() and len(phone) >= 10:
                    if len(phone) == 10:
                        phone = f"+91{phone}"
                    else:
                        phone = f"+{phone}"
                elif phone.startswith("91") and len(phone) == 12 and phone.isdigit():
                    phone = f"+{phone}"
                elif not phone.startswith("+") and phone.replace("+", "").replace("-", "").isdigit():
                    phone = f"+{phone}"
            
            # Normalize timestamp
            timestamp = lead_data.get("created_at")
            if isinstance(timestamp, datetime):
                timestamp = timestamp.isoformat()
            elif timestamp is None:
                timestamp = datetime.now(timezone.utc).isoformat()
            
            # Determine City (extract from interest if empty/None/placeholder)
            city = str(lead_data.get("city", "")).strip()
            interest = str(lead_data.get("interest", lead_data.get("property_interest", ""))).strip()
            if not city or city.lower() in ["none", "null", ""]:
                city = extract_city_from_interest(interest)
                
            # Build new CSV row
            new_row = {
                "Timestamp": timestamp,
                "Source": source,
                "Name": str(lead_data.get("name", lead_data.get("customer_name", "Unknown"))),
                "Phone": phone,
                "Email": str(lead_data.get("email", "")),
                "City": city,
                "Property Interest": interest,
                "Budget": str(lead_data.get("budget", "")),
                "Notes": str(lead_data.get("notes", "")),
                "CallID": call_id
            }
            
            # Read existing rows
            rows = []
            updated = False
            
            if os.path.exists(CSV_FILE_PATH):
                with open(CSV_FILE_PATH, "r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # DEDUPLICATION LOGIC:
                        # 1. Match by CallID (Perfect match)
                        # 2. Match by Phone AND it happened within last 30 mins (fallback)
                        
                        is_match = False
                        if call_id and row.get("CallID") == call_id:
                            is_match = True
                        elif not call_id and phone and row.get("Phone") == phone:
                            # Time-based fallback for WhatsApp or calls without SIDs
                            try:
                                row_time = datetime.fromisoformat(row.get("Timestamp", ""))
                                now_time = datetime.now(timezone.utc)
                                if (now_time - row_time).total_seconds() < 1800: # 30 mins
                                    is_match = True
                            except:
                                pass
                        
                        if is_match and not updated:
                            # Update the existing row with new non-empty data
                            for key in CSV_COLUMNS:
                                if key == "Timestamp": continue # Keep original timestamp
                                val = new_row.get(key)
                                # Only update if new value is more informative than old one
                                if val and val != "None" and val != "":
                                    row[key] = val
                            rows.append(row)
                            updated = True
                        else:
                            rows.append(row)
            
            # If not updated, append the new row
            if not updated:
                rows.append(new_row)
            
            # Write all rows back to file
            with open(CSV_FILE_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            
            return True
            
        except Exception as e:
            print(f"[CSV_LOGGER] ERROR: Failed to log lead: {e}")
            return False


def get_leads_csv_path() -> str:
    """
    Get the absolute path to the leads CSV file.
    """
    return CSV_FILE_PATH


def read_leads_csv() -> list[dict[str, str]]:
    """
    Read all leads from the CSV file.
    """
    with _lock:
        try:
            if not os.path.exists(CSV_FILE_PATH):
                return []
            
            leads = []
            with open(CSV_FILE_PATH, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    leads.append(row)
            
            return leads
            
        except Exception as e:
            print(f"[CSV_LOGGER] ERROR: Failed to read leads: {e}")
            return []