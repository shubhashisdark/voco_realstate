"""
Utility for pushing leads to Zoho CRM.
Handles OAuth2 token refreshing and lead creation.
"""

import aiohttp
import logging
import time
from config import Config

logger = logging.getLogger(__name__)

# In-memory cache for the access token
_access_token = None
_token_expiry = 0

async def get_access_token():
    """
    Get a valid access token for Zoho CRM API.
    Uses the refresh token to get a new access token if the current one is expired.
    """
    global _access_token, _token_expiry
    
    # Check if we have a valid cached token (with 5 min grace period)
    if _access_token and time.time() < (_token_expiry - 300):
        return _access_token
    
    logger.info("Refreshing Zoho CRM access token...")
    
    url = f"https://accounts.zoho.in/oauth/v2/token"
    params = {
        "refresh_token": Config.ZOHO_REFRESH_TOKEN,
        "client_id": Config.ZOHO_CLIENT_ID,
        "client_secret": Config.ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params) as response:
                data = await response.json()
                
                if "access_token" in data:
                    _access_token = data["access_token"]
                    # Default expiry is 1 hour (3600 seconds)
                    _token_expiry = time.time() + data.get("expires_in", 3600)
                    logger.info("Successfully refreshed Zoho access token")
                    return _access_token
                else:
                    logger.error(f"Failed to refresh Zoho token: {data}")
                    return None
    except Exception as e:
        logger.error(f"Error refreshing Zoho token: {e}")
        return None

async def push_lead_to_zoho(lead_data: dict):
    """
    Push a lead document to Zoho CRM Leads module.
    
    Args:
        lead_data: Dictionary containing lead details (name, phone, email, interest, budget, notes)
    """
    if not Config.ZOHO_REFRESH_TOKEN:
        logger.warning("Zoho integration skipped: No refresh token configured")
        return False
    
    token = await get_access_token()
    if not token:
        logger.error("Zoho integration failed: Could not get access token")
        return False
    
    # Map our lead fields to Zoho CRM Lead fields
    # Standard Zoho Lead fields: Last_Name (Required), First_Name, Email, Phone, Description, Lead_Source
    
    # Split name into first and last
    full_name = (lead_data.get("name") or lead_data.get("contactName") or "Unknown").strip()
    name_parts = full_name.split(" ", 1)
    first_name = name_parts[0] if len(name_parts) > 1 else ""
    last_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]
    
    # Format description with property interest and budget
    description = f"Interest: {lead_data.get('interest', 'N/A')}\n"
    description += f"Budget: {lead_data.get('budget', 'N/A')}\n"
    if lead_data.get("notes"):
        description += f"Notes: {lead_data['notes']}"
    
    zoho_lead = {
        "data": [
            {
                "First_Name": first_name,
                "Last_Name": last_name,
                "Email": lead_data.get("email", ""),
                "Phone": lead_data.get("phone") or lead_data.get("phoneNumber") or "",
                "Description": description,
                "Lead_Source": "Voice Agent (CodeMate AI)",
                "Lead_Status": "Not Contacted"
            }
        ],
        "trigger": ["workflow"] # Trigger Zoho workflows (email alerts, etc.)
    }
    
    url = f"{Config.ZOHO_ORG_DOMAIN.rstrip('/')}/crm/v3/Leads"
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=zoho_lead, headers=headers) as response:
                result = await response.json()
                
                if response.status in (200, 201) and "data" in result:
                    status = result["data"][0].get("status")
                    if status == "success":
                        zoho_id = result["data"][0].get("details", {}).get("id")
                        logger.info(f"Successfully pushed lead to Zoho CRM. ID: {zoho_id}")
                        return True
                    else:
                        logger.error(f"Zoho CRM returned error status: {result}")
                else:
                    logger.error(f"Failed to push lead to Zoho CRM. Status: {response.status}, Response: {result}")
                return False
    except Exception as e:
        logger.error(f"Error pushing lead to Zoho CRM: {e}")
        return False
