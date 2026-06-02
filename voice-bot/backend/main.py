"""
FastAPI application for Gemini Live Voice Agent Backend.
Bridges Twilio phone calls to Gemini's native audio capabilities.
"""

import asyncio
import difflib
import json
import math
import os
import re
import struct
import sys
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

# Force unbuffered output BEFORE any imports that might use logging
os.environ["PYTHONUNBUFFERED"] = "1"

# DEBUG FILE LOGGER - writes to disk for guaranteed visibility
import datetime as dt_mod
_debug_log_path = os.path.join(os.path.dirname(__file__), "debug_trace.log")
def _debug_write(msg):
    try:
        with open(_debug_log_path, "a") as _f:
            _f.write(f"{dt_mod.datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass

_debug_write("=== DEBUG LOGGER INIT ===")

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Query, Depends

# Configure logging with unbuffered stdout
# Create a custom handler that flushes after every log
class UnbufferedStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            sys.stdout.write(msg + '\n')
            sys.stdout.flush()
            # Also write to debug file
            _debug_write(msg)
        except Exception:
            pass

# Set up root logger first
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Remove any existing handlers to avoid duplicates
root_logger.handlers.clear()

# Add our unbuffered handler
handler = UnbufferedStreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s"))
root_logger.addHandler(handler)

# Also add a print wrapper for immediate visibility
def info(msg):
    print(f"[INFO] {msg}")
    sys.stdout.flush()

def error(msg):
    print(f"[ERROR] {msg}")
    sys.stdout.flush()

def warning(msg):
    print(f"[WARNING] {msg}")
    sys.stdout.flush()

logger = logging.getLogger(__name__)

# Log that logging is initialized
logger.info("Logging initialized with unbuffered output")

# Configure uvicorn logs
uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.setLevel(logging.INFO)
uvicorn_error_logger = logging.getLogger("uvicorn.error")
uvicorn_error_logger.setLevel(logging.INFO)
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.setLevel(logging.WARNING)

# Ensure all sub-loggers use our handler
for name in list(logging.root.manager.loggerDict):
    if name.startswith("uvicorn") or name.startswith("fastapi"):
        logging.getLogger(name).setLevel(logging.INFO)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from starlette.responses import Response
from pydantic import BaseModel
from aiohttp import ClientSession, ClientTimeout, BasicAuth
from twilio.rest import Client as TwilioClient

from config import Config
from audio_utils import (
    twilio_to_gemini_audio,
    gemini_to_twilio_audio,
    base64_decode_audio,
    base64_encode_audio,
)
from gemini_live import gemini_manager, analyze_sentiment_from_transcript
from whatsapp_sender import (
    send_whatsapp,
    send_whatsapp_message,
    send_meta_text_message,
    close_session,
    send_whatsapp_summary_and_properties,
    send_appointment_reminder,
    send_property_pack,
    send_followup_survey,
)
from whatsapp_bot import WhatsAppConversationManager
from zoho_crm import push_lead_to_zoho
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from bson import ObjectId
from csv_logger import log_lead_to_csv, get_leads_csv_path, CSV_COLUMNS


# ─── Budget Parsing Helper ────────────────────────────────────────────

def parse_budget_to_number(budget_str: str) -> float | None:
    """
    Convert budget strings like "50 Lakhs", "1.5 CR", "75L", "under 1 crore" into numerical values.
    Returns the value in rupees as a float, or None if parsing fails.
    
    Supported formats:
    - "50 Lakhs", "50 Lakh", "50L", "50 L" -> 50,000,000
    - "1.5 CR", "1.5 Cr", "1.5CR", "2 Crore", "2 Crores" -> 150,000,000
    - "50000000", "50000000" -> 50000000 (direct number)
    - "under 1 crore", "around 60 lakhs", "up to 75L" -> extracts the number
    - "1000000" -> 1000000 (already numeric)
    """
    if not budget_str or not isinstance(budget_str, str):
        return None
    
    # Clean the input
    budget_str = budget_str.strip().lower()
    
    # Remove common prefixes/suffixes
    budget_str = re.sub(r'^(under|around|up to|about|approximately|less than|more than|above|below)\s+', '', budget_str)
    
    # Extract numeric value and unit
    # Pattern to match numbers (including decimals) with optional unit
    pattern = r'([\d.,]+)\s*(lakh|lakhs|l|cr|crore|crores|c)?'
    match = re.search(pattern, budget_str)
    
    if not match:
        # Try to parse as a plain number
        try:
            return float(budget_str.replace(',', ''))
        except ValueError:
            return None
    
    number_str = match.group(1).replace(',', '')
    unit = (match.group(2) or '').strip()
    
    try:
        number = float(number_str)
    except ValueError:
        return None
    
    # Apply multiplier based on unit
    if unit in ['lakh', 'lakhs', 'l']:
        return number * 100000  # 1 Lakh = 100,000
    elif unit in ['cr', 'crore', 'crores', 'c']:
        return number * 10000000  # 1 Crore = 10,000,000
    else:
        # No unit specified - assume the number is already in rupees
        return number


# ─── City Resolution Helper (prefer DB-known city names) ──────────────────

_CITY_ALIAS_OVERRIDES: dict[str, str] = {
    # Baleswar / Balasore -> Balasore, Odisha (DB has "Balasore, Odisha")
    "baleshwar": "balasoreodisha",
    "baleswar": "balasoreodisha",
    "balasor": "balasoreodisha",
    "balasore": "balasoreodisha",
    "balasoreodisha": "balasoreodisha",

    # Bengaluru -> Bangalore (DB has "Bangalore")
    "bengaluru": "bangalore",
    "bengalooru": "bangalore",
    "banglore": "bangalore",
    "bangalore": "bangalore",

    # Gurugram -> Gurgaon (DB has "Gurgaon")
    "gurugram": "gurgaon",
    "gurgaon": "gurgaon",

    # Thiruvananthapuram -> Trivandrum (DB has "Trivandrum")
    "thiruvananthapuram": "trivandrum",
    "trivandrum": "trivandrum",

    # Mysuru -> Mysore (DB has "Mysore")
    "mysuru": "mysore",
    "mysore": "mysore",

    # Panjim -> Panaji (DB has "Panaji")
    "panjim": "panaji",
    "panaji": "panaji",

    # Calicut -> Kozhikode (DB has "Kozhikode")
    "calicut": "kozhikode",
    "kozhikode": "kozhikode",
}


def _normalize_city_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _resolve_city_from_candidates(city_input: str, candidates: list[str]) -> str:
    """Resolve spoken/transcribed city to the closest DB city without inventing new names."""
    requested = (city_input or "").strip()
    if not requested or not candidates:
        return requested

    norm_to_city: dict[str, str] = {}
    for city in candidates:
        if isinstance(city, str) and city.strip():
            norm_to_city[_normalize_city_key(city)] = city.strip()

    if not norm_to_city:
        return requested

    requested_norm = _normalize_city_key(requested)
    requested_norm = _CITY_ALIAS_OVERRIDES.get(requested_norm, requested_norm)

    if requested_norm in norm_to_city:
        return norm_to_city[requested_norm]

    # Accept substring matches before fuzzy match (e.g., "north balasore").
    for norm_city, original_city in norm_to_city.items():
        if requested_norm and (requested_norm in norm_city or norm_city in requested_norm):
            return original_city

    fuzzy = difflib.get_close_matches(requested_norm, list(norm_to_city.keys()), n=1, cutoff=0.75)
    if fuzzy:
        return norm_to_city[fuzzy[0]]

    return requested


async def resolve_city_against_db(db_conn, city_input: str) -> str:
    """Map an incoming city string to a known DB city when possible."""
    requested = (city_input or "").strip()
    if not requested or db_conn is None:
        return requested

    try:
        city_candidates = await asyncio.to_thread(lambda: db_conn.properties.distinct("city"))
        city_candidates = [c for c in city_candidates if isinstance(c, str) and c.strip()]
        return _resolve_city_from_candidates(requested, city_candidates)
    except Exception as exc:
        logger.warning(f"[TOOL] City resolution skipped due to DB error: {exc}")
        return requested


# ─── Appointment Date Validation (IST, strict future only) ───────────────

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def validate_preferred_date_future_only(date_str: str) -> tuple[bool, str]:
    """
    Validate appointment date as YYYY-MM-DD and ensure it is today-or-future in IST.
    Returns (is_valid, message).
    """
    if not date_str or not isinstance(date_str, str):
        return False, "Please share the appointment date in YYYY-MM-DD format."

    cleaned = date_str.strip()
    try:
        preferred_date = datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return False, "Invalid date format. Please share date as YYYY-MM-DD."

    today_ist = datetime.now(IST_TZ).date()
    if preferred_date < today_ist:
        return False, "Past dates are not allowed. Please choose today or a future date."

    return True, "ok"


# ─── MongoDB Connection ───────────────────────────────────────────────

mongo_client: MongoClient | None = None
db = None


def get_db():
    """Get MongoDB database instance with detailed error logging."""
    global mongo_client, db
    if db is None:
        try:
            logger.info(f"Connecting to MongoDB: {Config.MONGO_URI[:20]}...")
            mongo_client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
            # Force connection check
            mongo_client.server_info()
            db = mongo_client[Config.MONGO_DB_NAME]
            logger.info(f"Successfully connected to MongoDB database: {Config.MONGO_DB_NAME}")
        except Exception as e:
            logger.critical(f"Failed to connect to MongoDB: {str(e)}")
            db = None
    return db


async def find_properties_for_whatsapp(db_conn, search_terms: list[str] | None = None, limit: int = 5) -> list:
    """Look up properties for WhatsApp follow-up using the best available call hints."""
    if db_conn is None:
        return []

    cleaned_terms = [term.strip() for term in (search_terms or []) if term and term.strip()]
    if not cleaned_terms:
        return []

    query_clauses = []
    for term in cleaned_terms:
        # Check if term looks like a BHK type
        bhk_match = re.search(r'(\d)\s*BHK', term, re.I)
        if bhk_match:
            term_regex = f"{bhk_match.group(1)}\\s*BHK"
        else:
            term_regex = term

        query_clauses.extend([
            {"project_name": {"$regex": term_regex, "$options": "i"}},
            {"location": {"$regex": term_regex, "$options": "i"}},
            {"city": {"$regex": term_regex, "$options": "i"}},
            {"type": {"$regex": term_regex, "$options": "i"}},
            {"price": {"$regex": term_regex, "$options": "i"}},
        ])

    query = {"$or": query_clauses} if query_clauses else {}
    properties_cursor = await asyncio.to_thread(
        lambda: list(db_conn.properties.find(query).limit(limit))
    )
    return list(properties_cursor)


# ─── Pydantic Models ──────────────────────────────────────────────────

class OutboundCallRequest(BaseModel):
    """Request model for outbound call."""
    phone_number: str | None = None
    phoneNumbers: list[str] | None = None  # Legacy support for frontend array format
    contact_id: str | None = None
    script_id: str | None = None


class ContactRequest(BaseModel):
    """Request model for creating/updating a contact."""
    name: str
    phone: str
    email: str | None = None
    property_interest: str | None = None
    budget: str | None = None
    timeline: str | None = None
    status: str = "pending"
    notes: str | None = None

class CustomerDetailsRequest(BaseModel):
    """Request model for saving customer details from call detail UI."""
    contactName: str | None = None
    phoneNumber: str | None = None
    interest: str | None = None
    sentiment: str | None = None
    sentimentDescription: str | None = None
    notes: str | None = None
    callId: str | None = None
    callSid: str | None = None
    twilioCallSid: str | None = None
    recordingSid: str | None = None
    recordingUrl: str | None = None
    callDetails: dict | None = None


class SentimentData(BaseModel):
    """Request model for test sentiment."""
    phone: str
    sentiment: str = "positive"
    call_sid: str | None = None


class PendingCallRequest(BaseModel):
    """Request model for pending call."""
    phone: str
    scheduled_at: str
    status: str = "pending"


class PropertyRequest(BaseModel):
    """Request model for creating/updating a property."""
    project_name: str
    developer: str | None = None
    location: str
    city: str
    type: str  # 1BHK, 2BHK, 3BHK, Villa, Plot
    size_sqft: int | None = None
    price: str
    price_value: float | None = None
    amenities: str | None = None
    status: str = "Available"
    floors: int | None = None
    possession_date: str | None = None
    description: str | None = None


class PropertySearchQuery(BaseModel):
    """Query model for searching properties."""
    location: str | None = None
    city: str | None = None
    property_type: str | None = None
    budget_min: str | None = None
    budget_max: str | None = None


# ─── Call State Management ────────────────────────────────────────────

# In-memory store for active call streams
active_streams: dict[str, WebSocket] = {}
call_sessions: dict[str, tuple] = {}  # call_sid -> (twilio_ws, gemini_session)
whatsapp_bot_manager: WhatsAppConversationManager | None = None


def _serialize_call_document(call: dict) -> dict:
    """Convert a MongoDB call document into JSON-safe frontend data."""
    if not call:
        return call

    call["_id"] = str(call["_id"])

    for field in ["created_at", "updated_at", "started_at", "ended_at"]:
        if field in call and call[field]:
            call[field] = call[field].isoformat()

    return call


def _normalize_phone_key(value: str | None) -> str:
    """Normalize phone values to digits only for lookup purposes."""
    return re.sub(r"\D", "", (value or ""))


def _build_contact_sentiment_indexes(contacts: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build lookup tables for sentiment by phone and by call identifier."""
    by_phone: dict[str, dict] = {}
    by_call: dict[str, dict] = {}

    for contact in contacts:
        sentiment = contact.get("sentiment") or "neutral"
        sentiment_description = contact.get("sentimentDescription") or contact.get("sentiment_description") or ""
        payload = {
            "sentiment": sentiment,
            "sentiment_description": sentiment_description,
        }

        phone_candidates = [
            contact.get("phoneNumber"),
            contact.get("phone"),
            contact.get("customer_phone"),
        ]
        for phone_value in phone_candidates:
            phone_key = _normalize_phone_key(phone_value)
            if phone_key:
                by_phone[phone_key] = payload

        call_candidates = [
            contact.get("callSid"),
            contact.get("callId"),
            contact.get("twilioCallSid"),
        ]
        for call_value in call_candidates:
            call_key = (call_value or "").strip()
            if call_key:
                by_call[call_key] = payload

    return by_phone, by_call


def _apply_sentiment_fallback(call: dict, contact_by_phone: dict[str, dict], contact_by_call: dict[str, dict]) -> dict:
    """Fill missing call sentiment fields from related contact records."""
    if not call:
        return call

    sentiment_payload = None

    for key in [call.get("call_sid"), call.get("twilio_call_sid")]:
        call_key = (key or "").strip()
        if call_key and call_key in contact_by_call:
            sentiment_payload = contact_by_call[call_key]
            break

    if sentiment_payload is None:
        for key in [call.get("phone_number"), call.get("customer_phone"), call.get("phone")]:
            phone_key = _normalize_phone_key(key)
            if phone_key and phone_key in contact_by_phone:
                sentiment_payload = contact_by_phone[phone_key]
                break

    if sentiment_payload:
        current_sentiment = call.get("sentiment")
        if not current_sentiment or current_sentiment == "neutral":
            call["sentiment"] = sentiment_payload.get("sentiment", "neutral")
        
        current_desc = call.get("sentiment_description")
        if not current_desc or current_desc == "":
            call["sentiment_description"] = sentiment_payload.get("sentiment_description", "")
    else:
        call.setdefault("sentiment", "neutral")
        call.setdefault("sentiment_description", "")

    return call


def _call_lookup_filter(call_sid_value: str) -> dict:
    """Match either the stored stream SID or the Twilio call SID."""
    return {
        "$or": [
            {"call_sid": call_sid_value},
            {"twilio_call_sid": call_sid_value},
        ]
    }


async def _save_or_update_contact(db, phone: str, update_fields: dict) -> dict:
    """
    Finds a contact by matching either 'phone' or 'phoneNumber' with the provided phone string,
    and updates/upserts it cleanly without producing duplicates.
    """
    if db is None or not phone:
        return None

    clean_phone = phone.strip()
    if clean_phone and not clean_phone.startswith("+"):
        clean_phone = f"+{clean_phone}"

    # Always ensure both keys are set in the update fields
    update_fields["phone"] = clean_phone
    update_fields["phoneNumber"] = clean_phone
    update_fields["updated_at"] = datetime.now(timezone.utc)

    # Search for an existing document matching either field
    existing = await asyncio.to_thread(
        db.contacts.find_one,
        {
            "$or": [
                {"phoneNumber": clean_phone},
                {"phone": clean_phone}
            ]
        }
    )

    if existing:
        await asyncio.to_thread(
            db.contacts.update_one,
            {"_id": existing["_id"]},
            {"$set": update_fields}
        )
        return {"id": str(existing["_id"]), "action": "update"}
    else:
        update_fields["created_at"] = datetime.now(timezone.utc)
        result = await asyncio.to_thread(
            db.contacts.insert_one,
            update_fields
        )
        return {"id": str(result.inserted_id), "action": "insert"}


# ─── FastAPI App ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("=" * 80)
    logger.info("VOCO VOICE AGENT - STARTUP")
    logger.info("=" * 80)
    
    # Server Configuration
    logger.info(f"[SERVER] Host: {Config.SERVER_HOST}, Port: {Config.SERVER_PORT}")
    if Config.EXTERNAL_URL:
        logger.info(f"[SERVER] External URL: {Config.EXTERNAL_URL}")
    else:
        logger.warning(f"[SERVER] EXTERNAL_URL not set! Twilio won't be able to reach this server.")
        logger.info(f"[SERVER] Recommended: Set EXTERNAL_URL to your ngrok/public URL (e.g., https://abc123.ngrok-free.dev)")
    
    # Google API Configuration
    api_key_display = f"{Config.GOOGLE_API_KEY[:8]}..." if Config.GOOGLE_API_KEY else "NOT SET"
    logger.info(f"[GOOGLE] API Key: {api_key_display}")
    logger.info(f"[GOOGLE] Model: {Config.GEMINI_MODEL}")
    logger.info(f"[GOOGLE] Voice: {Config.GEMINI_VOICE}")
    logger.info(f"[GOOGLE] Thinking: {Config.GEMINI_THINKING}")
    
    # Twilio Configuration
    if Config.TWILIO_ACCOUNT_SID:
        logger.info(f"[TWILIO] Account SID: {Config.TWILIO_ACCOUNT_SID[:10]}...")
    else:
        logger.warning("[TWILIO] TWILIO_ACCOUNT_SID not set!")
    
    if Config.TWILIO_PHONE_NUMBER:
        logger.info(f"[TWILIO] Phone Number: {Config.TWILIO_PHONE_NUMBER}")
    else:
        logger.warning("[TWILIO] TWILIO_PHONE_NUMBER not set!")
    
    # WhatsApp Provider Configuration
    if Config.WHATSAPP_PROVIDER == "meta":
        if Config.META_WHATSAPP_ACCESS_TOKEN:
            token_display = f"{Config.META_WHATSAPP_ACCESS_TOKEN[:20]}..."
            logger.info(f"[WHATSAPP] Provider: Meta")
            logger.info(f"[WHATSAPP] Access Token: {token_display}")
        else:
            logger.warning("[WHATSAPP] META_WHATSAPP_ACCESS_TOKEN not set!")
        
        if Config.META_WHATSAPP_PHONE_NUMBER_ID:
            logger.info(f"[WHATSAPP] Phone Number ID: {Config.META_WHATSAPP_PHONE_NUMBER_ID}")
        else:
            logger.warning("[WHATSAPP] META_WHATSAPP_PHONE_NUMBER_ID not set!")
        
        if Config.META_WHATSAPP_PHONE_NUMBER:
            logger.info(f"[WHATSAPP] WhatsApp Number: {Config.META_WHATSAPP_PHONE_NUMBER}")
    elif Config.WHATSAPP_PROVIDER == "twilio":
        if Config.TWILIO_WHATSAPP_NUMBER:
            logger.info(f"[WHATSAPP] Provider: Twilio")
            logger.info(f"[WHATSAPP] WhatsApp Number: {Config.TWILIO_WHATSAPP_NUMBER}")
        else:
            logger.warning("[WHATSAPP] TWILIO_WHATSAPP_NUMBER not set!")
    else:
        logger.warning(f"[WHATSAPP] Unknown provider: {Config.WHATSAPP_PROVIDER}")
    
    # MongoDB Configuration
    mongo_display = f"{Config.MONGO_URI[:20]}..." if Config.MONGO_URI else "NOT SET"
    logger.info(f"[MONGODB] URI: {mongo_display}")
    logger.info(f"[MONGODB] Database: {Config.MONGO_DB_NAME}")
    
    # Test MongoDB Connection
    try:
        test_client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=3000)
        test_client.server_info()
        logger.info("[MONGODB] Connection: SUCCESS (OK)")
        test_client.close()
    except Exception as e:
        logger.error(f"[MONGODB] Connection: FAILED (X) - {e}")
    
    # API Key Status
    if Config.API_KEY:
        key_display = f"{Config.API_KEY[:8]}..."
        logger.info(f"[AUTH] API Key: Set ({key_display})")
    else:
        logger.info("[AUTH] API Key: Not set (Authentication disabled)")
    
    # Audio Configuration
    logger.info(f"[AUDIO] Twilio: {Config.TWILIO_SAMPLE_RATE}Hz {Config.TWILIO_ENCODING.upper()}")
    logger.info(f"[AUDIO] Gemini Input: {Config.GEMINI_SAMPLE_RATE}Hz PCM")
    logger.info(f"[AUDIO] Gemini Output: {Config.GEMINI_OUTPUT_SAMPLE_RATE}Hz PCM")
    
    # System Prompt Status
    prompt_length = len(Config.SYSTEM_PROMPT)
    logger.info(f"[SYSTEM] System Prompt: {prompt_length} characters")
    
    # Config Validation
    errors = Config.validate()
    if errors:
        logger.warning("[CONFIG] Validation warnings:")
        for error in errors:
            logger.warning(f"  - {error}")
    else:
        logger.info("[CONFIG] All required settings: VALID (OK)")
    
    logger.info("=" * 80)
    logger.info("VOCO Voice Agent is ready to accept calls!")
    logger.info("=" * 80)
    
    yield
    
    # Shutdown
    logger.info("=" * 80)
    logger.info("Shutting down Gemini Voice Agent")
    logger.info("=" * 80)
    # Close all active Gemini sessions
    for call_sid in list(gemini_manager.active_sessions.keys()):
        await gemini_manager.close_session(call_sid)


app = FastAPI(
    title="Gemini Live Voice Agent",
    description="Twilio + Gemini 3.1 Flash Live Voice Agent Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── API Key Authentication ────────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Depends(api_key_header)):
    """Verify API key if configured. Skips auth when API_KEY is not set."""
    if not Config.API_KEY:
        return True  # Auth disabled when no key configured
    if key != Config.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True


# ─── Twilio Webhook Endpoints ─────────────────────────────────────────

@app.post("/incoming-call")
async def incoming_call(request: Request):
    """
    Twilio webhook for incoming calls.
    Returns TwiML that instructs Twilio to stream audio to our WebSocket endpoint.
    """
    _debug_write("[WEBHOOK] >>> /incoming-call HIT!")
    print("[WEBHOOK] >>> /incoming-call HIT!", flush=True)
    logger.info("[WEBHOOK] /incoming-call received!")
    from twilio.twiml.voice_response import VoiceResponse
    
    # Use Config helper for proper URL formatting
    base_url = Config.get_external_url()
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    
    # Extract customer's phone number from the inbound call form data
    form_data = await request.form()
    customer_phone = form_data.get("From", "")
    
    response = VoiceResponse()
    response.say("Connecting you to our AI assistant. Please wait.")
    connect = response.connect()
    
    ws_url_with_phone = f"{ws_url}/ws/twilio"
    if customer_phone:
        ws_url_with_phone += f"?From={customer_phone}"
    connect.stream(url=ws_url_with_phone)
    
    # Return as proper XML with TwiML content type
    logger.info(f"[WEBHOOK] /incoming-call returning TwiML: {ws_url_with_phone}")
    return Response(content=str(response), media_type="application/xml")


def parse_phone_numbers(input_str_or_list) -> list[str]:
    """Parse comma-separated number strings or lists of phone numbers into formatted E.164 strings."""
    if not input_str_or_list:
        return []
    raw_numbers = []
    if isinstance(input_str_or_list, list):
        for item in input_str_or_list:
            if isinstance(item, str):
                raw_numbers.extend(item.split(","))
    elif isinstance(input_str_or_list, str):
        raw_numbers.extend(input_str_or_list.split(","))
    
    cleaned = []
    for num in raw_numbers:
        num_str = num.strip()
        if num_str:
            if not num_str.startswith("+"):
                if len(num_str) == 10 and num_str.isdigit():
                    num_str = f"+91{num_str}"
                else:
                    num_str = f"+{num_str}"
            cleaned.append(num_str)
    return cleaned


async def _trigger_outbound_call(phone_number: str, contact_id: str | None = None) -> dict:
    """Shared logic for triggering a single outbound call via Twilio."""
    clean_number = phone_number.strip()
    if clean_number and not clean_number.startswith("+"):
        if len(clean_number) == 10 and clean_number.isdigit():
            clean_number = f"+91{clean_number}"
        else:
            clean_number = f"+{clean_number}"
            
    # Ensure memory card exists and is fully populated/synced
    db = get_db()
    if db is not None:
        existing = await asyncio.to_thread(
            db.contacts.find_one,
            {
                "$or": [
                    {"phoneNumber": clean_number},
                    {"phone": clean_number}
                ]
            }
        )
        if not existing:
            # Create default contact card
            contact_doc = {
                "name": "Unknown Customer",
                "contactName": "Unknown Customer",
                "phone": clean_number,
                "phoneNumber": clean_number,
                "email": "",
                "property_interest": "",
                "budget": "",
                "city": "",
                "sentiment": "neutral",
                "notes": "Initiated via Make Call UI page.",
                "lead_saved": True,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            await asyncio.to_thread(db.contacts.insert_one, contact_doc)
            
            # PUSH to Zoho CRM
            lead_data_zoho = {
                "name": "Unknown Customer",
                "phone": clean_number,
                "email": "",
                "city": "",
                "interest": "Outbound Lead",
                "budget": "",
                "notes": "Outbound call initiated from VOCO Make Call UI.",
                "source": "voice_call",
                "created_at": datetime.now(timezone.utc)
            }
            asyncio.create_task(push_lead_to_zoho(lead_data_zoho))
            
            # LOG to Leads CSV
            asyncio.create_task(
                asyncio.to_thread(log_lead_to_csv, lead_data_zoho, "voice")
            )
            logger.info(f"[OUTBOUND] Created default contact, pushed to Zoho & CSV for {clean_number}")
        else:
            # Update updated_at and lead_saved to ensure memory exists
            await asyncio.to_thread(
                db.contacts.update_one,
                {"_id": existing["_id"]},
                {"$set": {"updated_at": datetime.now(timezone.utc), "lead_saved": True}}
            )
            
            # Retrieve existing fields to push to Zoho / CSV if they are not already there
            lead_data_zoho = {
                "name": existing.get("contactName") or existing.get("name") or "Unknown Customer",
                "phone": clean_number,
                "email": existing.get("email") or "",
                "city": existing.get("city") or "",
                "interest": existing.get("interest") or existing.get("property_interest") or "Outbound Lead",
                "budget": existing.get("budget") or "",
                "notes": existing.get("notes") or "Outbound call initiated from VOCO Make Call UI.",
                "source": "voice_call",
                "created_at": existing.get("created_at") or datetime.now(timezone.utc)
            }
            asyncio.create_task(push_lead_to_zoho(lead_data_zoho))
            asyncio.create_task(
                asyncio.to_thread(log_lead_to_csv, lead_data_zoho, "voice")
            )
            logger.info(f"[OUTBOUND] Contact exists for {clean_number}. Updated updated_at, pushed memory to Zoho & CSV.")

    # Use Config helper for proper URL formatting
    outbound_twiml_url = Config.get_external_url("outbound-call-twiml")
    call_status_url = Config.get_external_url("api/call-status")

    def _create_call():
        client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
        return client.calls.create(
            to=clean_number,
            from_=Config.TWILIO_PHONE_NUMBER,
            url=outbound_twiml_url,
            status_callback=call_status_url,
        )

    call = await asyncio.to_thread(_create_call)

    db = get_db()
    if db is not None:
        # Create the call record
        call_doc = {
            "call_sid": call.sid,
            "phone_number": clean_number,
            "direction": "outbound",
            "status": "initiated",
            "contact_id": contact_id,
            "created_at": datetime.now(timezone.utc),
        }
        await asyncio.to_thread(db.calls.insert_one, call_doc)
        
        # ALSO insert into pending_calls so it shows up on the Dashboard immediately!
        await asyncio.to_thread(
            db.pending_calls.insert_one,
            {
                "callId": call.sid,
                "phoneNumber": clean_number,
                "status": "processing",
                "created_at": datetime.now(timezone.utc),
                "is_outbound": True
            }
        )

    return {"call_sid": call.sid, "status": call.status, "phone_number": clean_number}


@app.post("/outbound-call-twiml")
async def outbound_call_twiml(request: Request):
    """
    TwiML for outbound calls — no say() greeting, Gemini handles that directly.
    """
    _debug_write("[WEBHOOK] >>> /outbound-call-twiml HIT!")
    print("[WEBHOOK] >>> /outbound-call-twiml HIT!", flush=True)
    logger.info("[WEBHOOK] /outbound-call-twiml received!")
    from twilio.twiml.voice_response import VoiceResponse

    # Use Config helper for proper URL formatting
    base_url = Config.get_external_url()
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")

    # Extract customer's phone number from the outbound call form data
    form_data = await request.form()
    customer_phone = form_data.get("To", "")

    response = VoiceResponse()
    connect = response.connect()

    ws_url_with_phone = f"{ws_url}/ws/twilio"
    if customer_phone:
        ws_url_with_phone += f"?To={customer_phone}"
    connect.stream(url=ws_url_with_phone)

    logger.info(f"[WEBHOOK] /outbound-call-twiml returning TwiML: {ws_url_with_phone}")
    return Response(content=str(response), media_type="application/xml")


@app.post("/api/outbound-call")
async def make_outbound_call(request: OutboundCallRequest, auth: bool = Depends(verify_api_key)):
    """
    Trigger outbound call(s) via Twilio.
    Supports comma-separated numbers in phone_number or lists in phoneNumbers.
    """
    raw_number = request.phone_number or ""
    numbers = parse_phone_numbers(raw_number)
    
    if request.phoneNumbers:
        numbers.extend(parse_phone_numbers(request.phoneNumbers))
    
    # Deduplicate keeping order
    seen = set()
    numbers = [x for x in numbers if not (x in seen or seen.add(x))]
    
    if not numbers:
        raise HTTPException(status_code=400, detail="No phone numbers provided or could be parsed")
    
    # Dispatch calls concurrently with a modest concurrency limit to avoid rate spikes.
    sem = asyncio.Semaphore(Config.MAX_CONCURRENT_CALLS)

    async def _call_wrapper(num: str):
        async with sem:
            return await _trigger_outbound_call(num, request.contact_id)

    tasks = [asyncio.create_task(_call_wrapper(number)) for number in numbers]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    success_count = 0
    last_call_sid = None

    for number, res in zip(numbers, completed):
        if isinstance(res, Exception):
            results.append({"number": number, "success": False, "error": str(res)})
        else:
            results.append({"number": number, "success": True, "data": {"id": res.get("call_sid")}})
            success_count += 1
            last_call_sid = res.get("call_sid") or last_call_sid

    return {
        "success": success_count > 0,
        "message": f"Successfully initiated {success_count} out of {len(numbers)} calls",
        "results": results,
        "call_sid": last_call_sid,
    }


@app.post("/api/nvidia/make-calls")
async def legacy_nvidia_make_calls(request: OutboundCallRequest, auth: bool = Depends(verify_api_key)):
    """
    Legacy compatibility for the frontend's NVIDIA provider endpoint.
    Bridges to our Gemini outbound call logic.
    """
    raw_number = request.phone_number or ""
    numbers = parse_phone_numbers(raw_number)
    if request.phoneNumbers:
        numbers.extend(parse_phone_numbers(request.phoneNumbers))
        
    seen = set()
    numbers = [x for x in numbers if not (x in seen or seen.add(x))]
    
    if not numbers:
        raise HTTPException(status_code=400, detail="No phone numbers provided")

    # Dispatch calls concurrently with a modest concurrency limit to avoid rate spikes.
    sem = asyncio.Semaphore(Config.MAX_CONCURRENT_CALLS)

    async def _call_wrapper(num: str):
        async with sem:
            return await _trigger_outbound_call(num)

    tasks = [asyncio.create_task(_call_wrapper(number)) for number in numbers]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    success_count = 0
    last_call_sid = None

    for number, res in zip(numbers, completed):
        if isinstance(res, Exception):
            results.append({"number": number, "success": False, "error": str(res)})
        else:
            results.append({"number": number, "success": True, "data": {"id": res.get("call_sid")}})
            success_count += 1
            last_call_sid = res.get("call_sid") or last_call_sid

    return {
        "success": success_count > 0,
        "message": f"Successfully initiated {success_count} out of {len(numbers)} calls",
        "results": results,
        "call_sid": last_call_sid,
    }


@app.post("/api/call-status")
async def call_status_update(request: Request):
    """
    Twilio callback for call status updates.
    """
    _debug_write("[WEBHOOK] >>> /api/call-status HIT!")
    print("[WEBHOOK] >>> /api/call-status HIT!", flush=True)
    logger.info("[WEBHOOK] /api/call-status received!")
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    logger.info(f"[WEBHOOK] /api/call-status: CallSid={call_sid}, Status={call_status}")
    
    # Update in MongoDB
    db = get_db()
    if db is not None and call_sid:
        recording_url = form_data.get("RecordingUrl")
        update_doc = {
            "status": call_status,
            "updated_at": datetime.now(timezone.utc),
        }
        if recording_url:
            update_doc["recording_url"] = recording_url

        await asyncio.to_thread(
            db.calls.update_one,
            _call_lookup_filter(call_sid),
            {"$set": update_doc},
        )
        logger.info(f"[WEBHOOK] /api/call-status: Updated call record in MongoDB")
    
    return {"status": "received"}


# ─── WebSocket Bridge ─────────────────────────────────────────────────

# Start/Shutdown Events
@app.on_event("shutdown")
async def shutdown_event():
    await close_session()
    logger.info("Application shutdown complete")

@app.websocket("/ws/twilio")
async def twilio_websocket(websocket: WebSocket):
    """
    Bidirectional bridge between Twilio Media Streams and Gemini Live API.
    
    - Inbound (Twilio → Gemini): Receives base64-encoded µ-law 8kHz audio,
      converts to PCM 16kHz, sends to Gemini.
    - Outbound (Gemini → Twilio): Receives PCM 24kHz from Gemini,
      converts to µ-law 8kHz, sends back to Twilio.
    """
    # CRITICAL: Log IMMEDIATELY when function is called
    _debug_write("### TWILIO_WEBSOCKET_ENDPOINT_HIT ###")
    print("### TWILIO_WEBSOCKET_ENDPOINT_HIT ###", flush=True)
    
    try:
        await websocket.accept()
        _debug_write("### WEBSOCKET_ACCEPTED ###")
        print("### WEBSOCKET_ACCEPTED ###", flush=True)
    except Exception as e:
        _debug_write(f"### WEBSOCKET_ACCEPT_FAILED: {e} ###")
        print(f"### WEBSOCKET_ACCEPT_FAILED: {e} ###", flush=True)
        return
    
    call_sid = None
    stream_sid = None
    real_call_sid = None  # The actual Twilio Call SID (for hangup)
    
    # Extract query parameters for phone numbers
    query_params = websocket.query_params
    customer_phone = query_params.get("To") or query_params.get("From") or query_params.get("phone") or None
    
    gemini_session = None
    pending_hangup = False  # Flag to defer hangup until AI finishes speaking
    call_properties = []  # Store properties fetched during call for WhatsApp sending
    call_appointment = {}  # Store appointment details booked during the call
    captured_interest = ""  # Track customer's property interest (type + location)
    captured_budget = ""  # Track customer's budget
    captured_city = ""  # Track customer's city

    try:
        # Generate a temporary ID until we get the real stream SID from Twilio
        call_sid = f"stream_{datetime.now(timezone.utc).timestamp()}"
        _debug_write(f"[WS] >>> WebSocket connected: {call_sid}")
        print(f"[WS] >>> WebSocket connected: {call_sid}", flush=True)
        logger.info(f"WebSocket connected: {call_sid}")
        
        active_streams[call_sid] = websocket
        
        # Create Gemini session dynamically when the 'start' event with customer phone is received.
        # This allows us to load their memory and dynamically inject system instructions.
        gemini_session = None
        gemini_session_ready = asyncio.Event()
        custom_prompt_cached = ""
        
        # Initialize call record in MongoDB
        db_conn = get_db()
        if db_conn is not None:
            logger.info(f"[DB] Attempting to create call record in collection 'calls' for {call_sid}")
            try:
                result = await asyncio.to_thread(
                    db_conn.calls.insert_one,
                    {
                        "call_sid": call_sid,
                        "twilio_call_sid": None,
                        "direction": "inbound",
                        "status": "connected",
                        "started_at": datetime.now(timezone.utc),
                        "ended_at": None,
                        "transcripts": [],
                        "customer_name": "Unknown Customer",
                        "customer_phone": "",
                        "recording_url": "",
                        "summary": "",
                        "sentiment": "neutral",
                        "sentiment_description": "",
                        "call_properties": [],
                        "call_appointment": {},
                    },
                )
                logger.info(f"[DB] SUCCESS: Call record created with ID: {result.inserted_id}")
            except Exception as e:
                logger.error(f"[DB] ERROR: Failed to create call record: {str(e)}")
        else:
            logger.warning(f"[DB] WARNING: Skipping call record for {call_sid} (No DB connection)")
        
        # Task 1: Receive audio from Twilio, convert, send to Gemini
        async def receive_from_twilio():
            """Receive media events from Twilio, convert, and send to Gemini."""
            nonlocal call_sid
            nonlocal stream_sid
            nonlocal real_call_sid
            nonlocal customer_phone
            nonlocal call_properties
            nonlocal call_appointment
            nonlocal pending_hangup
            nonlocal gemini_session
            nonlocal custom_prompt_cached
            nonlocal gemini_session_ready
            
            _debug_write(f"[TASK] receive_from_twilio() started")
            logger.info(f"[TASK] receive_from_twilio() started")
            
            message_count = 0
            consecutive_send_errors = 0
            MAX_SEND_ERRORS = 5
            
            async def try_reconnect_gemini():
                """Attempt to reconnect the Gemini session when it dies."""
                nonlocal gemini_session, consecutive_send_errors, custom_prompt_cached
                for attempt in range(1, 4):
                    try:
                        logger.warning(f"[RECONNECT] Attempting Gemini reconnection {attempt}/3...")
                        # Clean up old session
                        try:
                            old = gemini_manager.active_sessions.pop(call_sid, None)
                            if old:
                                old._shutdown_event.set()
                                old.is_connected = False
                        except Exception:
                            pass
                        # Create new session using the exact same customer memory context
                        gemini_session = await gemini_manager.create_session(call_sid, system_prompt=custom_prompt_cached)
                        call_sessions[call_sid] = (websocket, gemini_session)
                        consecutive_send_errors = 0
                        logger.info(f"[RECONNECT] SUCCESS on attempt {attempt}")
                        return True
                    except Exception as e:
                        logger.error(f"[RECONNECT] Failed attempt {attempt}: {e}")
                        if attempt < 3:
                            await asyncio.sleep(1)
                logger.error("[RECONNECT] All reconnection attempts failed")
                return False
            
            while True:
                try:
                    message_count += 1
                    _debug_write(f"[TWILIO] Waiting for message #{message_count}...")
                    raw_message = await websocket.receive_text()
                    data = json.loads(raw_message)
                    
                    event = data.get("event")
                    _debug_write(f"[TWILIO] Message #{message_count}: event={event}")
                    
                    if event == "start":
                        # Twilio sends 'start' with the real streamSid and callSid
                        stream_sid = data.get("streamSid", call_sid)
                        # Extract the real Twilio Call SID from the start metadata
                        start_data = data.get("start", {})
                        real_call_sid = start_data.get("callSid")
                        # Fall back to start_data 'from' if not found in query params
                        if not customer_phone:
                            customer_phone = start_data.get("from", "")
                        
                        # Normalize helper to compare numbers
                        def normalize_for_comparison(phone_str):
                            if not phone_str:
                                return ""
                            return "".join(c for c in phone_str if c.isdigit() or c == "+")
                        
                        # Clear customer_phone if it is the Twilio system number
                        if customer_phone:
                            normalized_customer = normalize_for_comparison(customer_phone)
                            normalized_twilio = normalize_for_comparison(Config.TWILIO_PHONE_NUMBER)
                            if normalized_customer == normalized_twilio:
                                customer_phone = ""

                        logger.info(f"[TWILIO] Stream started: {stream_sid} (Call SID: {real_call_sid}, Phone: {customer_phone})")
                        _debug_write(f"[TWILIO] Stream started: stream_sid={stream_sid}, call_sid={real_call_sid}, phone={customer_phone}")

                        # If phone not resolved yet, fetch from Twilio REST API
                        if real_call_sid and not customer_phone:
                            try:
                                async def _fetch_caller_phone():
                                    def _get_call_details():
                                        client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                                        call = client.calls(real_call_sid).fetch()
                                        # Check call direction to get the correct number field
                                        if call.direction and "outbound" in call.direction.lower():
                                            return call.to
                                        return call.from_formatted
                                    return await asyncio.to_thread(_get_call_details)
                                
                                customer_phone = await asyncio.wait_for(_fetch_caller_phone(), timeout=3.0)
                                logger.info(f"[TWILIO] Fetched caller phone from API: {customer_phone}")
                            except asyncio.TimeoutError:
                                logger.warning(f"[TWILIO] Timeout fetching caller phone for {real_call_sid}")
                            except Exception as e:
                                logger.warning(f"[TWILIO] Could not fetch caller phone: {e}")

                        # Dynamically load contact memory and inject custom system prompt instructions
                        custom_prompt = Config.SYSTEM_PROMPT
                        customer_name = ""
                        customer_interest = ""
                        customer_budget = ""
                        customer_city = ""
                        
                        target_phone = ""
                        # For outbound calls, fetch customer's phone from call doc or Twilio Rest API 'to' field
                        db_conn = get_db()
                        existing_call = None
                        if db_conn is not None and real_call_sid:
                            try:
                                existing_call = await asyncio.to_thread(
                                    db_conn.calls.find_one, {"call_sid": real_call_sid}
                                )
                            except Exception as e:
                                logger.error(f"[DB] Error finding call document: {e}")
                                
                        if existing_call:
                            target_phone = existing_call.get("phone_number")
                        if not target_phone and customer_phone:
                            if customer_phone != Config.TWILIO_PHONE_NUMBER:
                                target_phone = customer_phone
                                
                        if target_phone:
                            # Normalize target phone to search database
                            clean_target_phone = target_phone.strip()
                            if clean_target_phone and not clean_target_phone.startswith("+"):
                                if len(clean_target_phone) == 10 and clean_target_phone.isdigit():
                                    clean_target_phone = f"+91{clean_target_phone}"
                                else:
                                    clean_target_phone = f"+{clean_target_phone}"
                            
                            # CRITICAL: Overwrite customer_phone with clean customer number so cleanup task uses the correct one!
                            customer_phone = clean_target_phone
                                    
                            if db_conn is not None:
                                try:
                                    contact = await asyncio.to_thread(
                                        db_conn.contacts.find_one,
                                        {
                                            "$or": [
                                                {"phoneNumber": clean_target_phone},
                                                {"phone": clean_target_phone}
                                            ]
                                        }
                                    )
                                    if contact:
                                        customer_name = contact.get("contactName") or contact.get("name") or ""
                                        customer_interest = contact.get("interest") or contact.get("property_interest") or ""
                                        customer_budget = contact.get("budget") or ""
                                        customer_city = contact.get("city") or ""
                                        logger.info(f"[TWILIO] Found database memory for {clean_target_phone}: name={customer_name}, interest={customer_interest}")
                                    else:
                                        # New inbound call, create default contact doc immediately
                                        contact_doc = {
                                            "name": "Unknown Customer",
                                            "contactName": "Unknown Customer",
                                            "phone": clean_target_phone,
                                            "phoneNumber": clean_target_phone,
                                            "email": "",
                                            "property_interest": "",
                                            "budget": "",
                                            "city": "",
                                            "sentiment": "neutral",
                                            "notes": "Initiated via Incoming Twilio Call.",
                                            "lead_saved": True,
                                            "created_at": datetime.now(timezone.utc),
                                            "updated_at": datetime.now(timezone.utc)
                                        }
                                        await asyncio.to_thread(db_conn.contacts.insert_one, contact_doc)
                                        
                                        # PUSH to Zoho CRM
                                        lead_data_zoho = {
                                            "name": "Unknown Customer",
                                            "phone": clean_target_phone,
                                            "email": "",
                                            "city": "",
                                            "interest": "Inbound Lead",
                                            "budget": "",
                                            "notes": "Inbound call from Twilio.",
                                            "source": "voice_call",
                                            "created_at": datetime.now(timezone.utc)
                                        }
                                        asyncio.create_task(push_lead_to_zoho(lead_data_zoho))
                                        
                                        # LOG to Leads CSV
                                        asyncio.create_task(
                                            asyncio.to_thread(log_lead_to_csv, lead_data_zoho, "voice")
                                        )
                                        logger.info(f"[TWILIO] Created default contact, pushed to Zoho & CSV for {clean_target_phone}")
                                except Exception as db_err:
                                    logger.error(f"[TWILIO] Error reading contact card: {db_err}")

                        # Inject strict prompt guard and memory details at the top of the prompt
                        prompt_instructions = f"\n\nCUSTOMER PROFILE (Known details):\n"
                        if target_phone:
                            prompt_instructions += f"  • Phone Number : {target_phone} (THIS PHONE IS PRE-VERIFIED. NEVER ask the customer for their phone number under any circumstances!)\n"
                        if customer_name and customer_name.lower() != "unknown customer" and customer_name.lower() != "customer":
                            prompt_instructions += f"  • Name         : {customer_name} (THIS NAME IS PRE-VERIFIED. Address them by name and DO NOT ask for their name!)\n"
                        else:
                            prompt_instructions += f"  • Name         : Unknown Customer (You MUST ask the customer for their name in Phase 3. If it remains Unknown Customer, ask: 'Kya main aapka shubh naam jaan sakti hoon?')\n"
                        if customer_interest:
                            prompt_instructions += f"  • Property Interest: {customer_interest}\n"
                        if customer_budget:
                            prompt_instructions += f"  • Budget: {customer_budget}\n"
                        if customer_city:
                            prompt_instructions += f"  • City: {customer_city}\n"
                        prompt_instructions += "\n"
                            
                        # Replace variables
                        custom_prompt = prompt_instructions + custom_prompt
                        custom_prompt = custom_prompt.replace("{{CURRENT_DATE}}", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                        custom_prompt = custom_prompt.replace("{{CURRENT_TIME_IST}}", datetime.now(timezone.utc).strftime("%H:%M:%S"))
                        
                        # Cache dynamic system prompt for reconnection resilience
                        custom_prompt_cached = custom_prompt
                        
                        # Create dynamic Gemini live session
                        try:
                            gemini_session = await gemini_manager.create_session(call_sid, system_prompt=custom_prompt)
                            call_sessions[call_sid] = (websocket, gemini_session)
                            gemini_session_ready.set()
                            _debug_write(f"[WS] >>> Gemini session created dynamically for {call_sid}")
                            logger.info(f"Gemini session created dynamically for {call_sid}")
                        except Exception as e:
                            logger.error(f"FATAL: Deferred Gemini session creation failed: {e}")
                            await websocket.close()
                            return

                        # Trigger the greeting in the background once the Twilio media stream is active with real stream_sid
                        async def _send_greeting():
                            try:
                                # Address the customer by name if known and explicitly
                                # note that we already have their details to avoid asking again.
                                if customer_name and customer_name.lower() not in ("unknown customer", "customer", ""):
                                    greeting_text = (
                                        f"Namaste {customer_name}! Main VOCO bol rahi hoon, CodeMate AI ki taraf se. "
                                        "Maine aapke details hamare records me paye hain, isliye main aapse phir se aapka naam nahi poochunga. "
                                        "Main aapki property search ko simple aur hassle-free bana sakti hoon — kya main aapki madad kar sakti hoon?"
                                    )
                                else:
                                    greeting_text = (
                                        "Namaste! Main VOCO bol rahi hoon, CodeMate AI ki taraf se. "
                                        "Main aapki property search ko simple aur hassle-free bana sakti hoon — chahe aap buy, rent ya invest karna chahte ho. "
                                        "Batayein, main aapki kaise madad kar sakti hoon?"
                                    )

                                await gemini_session.send_text(greeting_text)
                                logger.info(f"Greeting sent successfully with active streamSid: {stream_sid}")
                            except Exception as ge:
                                logger.error(f"Error sending greeting text: {ge}")
                        asyncio.create_task(_send_greeting())

                        if real_call_sid or customer_phone:
                            db_conn = get_db()
                            if db_conn is not None:
                                try:
                                    # If real_call_sid is provided, check if a call document already exists for it
                                    existing_call = None
                                    if real_call_sid:
                                        existing_call = await asyncio.to_thread(
                                            db_conn.calls.find_one, {"call_sid": real_call_sid}
                                        )
                                    
                                    if existing_call:
                                        logger.info(f"[DB] Found existing outbound call document for real Call SID: {real_call_sid}. Merging temporary stream doc.")
                                        
                                        # Delete the temporary stream doc that was created at websocket accept
                                        await asyncio.to_thread(
                                            db_conn.calls.delete_one, {"call_sid": call_sid}
                                        )
                                        
                                        # Update our local call_sid variable to point to the real Twilio Call SID
                                        old_call_sid = call_sid
                                        call_sid = real_call_sid
                                        
                                        # Re-map active_streams and call_sessions to use the real Call SID
                                        if old_call_sid in active_streams:
                                            active_streams[real_call_sid] = active_streams.pop(old_call_sid)
                                        if old_call_sid in call_sessions:
                                            call_sessions[real_call_sid] = call_sessions.pop(old_call_sid)
                                        
                                        # Update the existing call document with the stream details
                                        update_doc = {
                                            "twilio_call_sid": real_call_sid,
                                            "status": "connected",
                                            "started_at": datetime.now(timezone.utc),
                                            "updated_at": datetime.now(timezone.utc),
                                            "transcripts": [],
                                            "customer_name": existing_call.get("customer_name") or "Unknown Customer",
                                            "customer_phone": customer_phone or existing_call.get("phone_number") or "",
                                            "summary": "",
                                            "sentiment": "neutral",
                                            "sentiment_description": "",
                                            "call_properties": [],
                                            "call_appointment": {},
                                        }
                                        # Try to resolve contact details if we have customer phone
                                        target_phone = customer_phone or existing_call.get("phone_number")
                                        if target_phone:
                                            contact = await asyncio.to_thread(
                                                db_conn.contacts.find_one, {
                                                    "$or": [
                                                        {"phoneNumber": target_phone},
                                                        {"phone": target_phone}
                                                    ]
                                                }
                                            )
                                            if contact:
                                                update_doc["customer_name"] = contact.get("contactName") or contact.get("name") or "Unknown Customer"
                                        
                                        await asyncio.to_thread(
                                            db_conn.calls.update_one,
                                            {"call_sid": real_call_sid},
                                            {"$set": update_doc},
                                        )
                                    else:
                                        # Standard flow: update the temporary stream doc
                                        update_doc = {"updated_at": datetime.now(timezone.utc)}
                                        if real_call_sid:
                                            update_doc["twilio_call_sid"] = real_call_sid
                                        if customer_phone:
                                            update_doc["customer_phone"] = customer_phone
                                            contact = await asyncio.to_thread(
                                                db_conn.contacts.find_one, {
                                                    "$or": [
                                                        {"phoneNumber": customer_phone},
                                                        {"phone": customer_phone}
                                                    ]
                                                }
                                            )
                                            if contact:
                                                update_doc["customer_name"] = contact.get("contactName") or contact.get("name") or "Unknown Customer"

                                        await asyncio.to_thread(
                                            db_conn.calls.update_one,
                                            {"call_sid": call_sid},
                                            {"$set": update_doc},
                                        )
                                except Exception as e:
                                    logger.error(f"[DB] ERROR: Failed to merge/save call stream phone: {str(e)}")

                        # If configured, start Twilio provider-side recording for the real call SID
                        if real_call_sid and Config.RECORD_CALLS:
                            try:
                                async def _start_recording_bg(twilio_call_sid, local_call_sid):
                                    try:
                                        def _start():
                                            client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                                            rec = client.calls(twilio_call_sid).recordings.create(
                                                recording_channels=Config.RECORDING_CHANNELS,
                                                recording_status_callback=Config.get_external_url(Config.RECORDING_STATUS_PATH),
                                                recording_status_callback_method="POST",
                                                trim="do-not-trim",
                                            )
                                            return rec.sid

                                        rec_sid = await asyncio.to_thread(_start)
                                        logger.info(f"[TWILIO] Recording started for call {twilio_call_sid}, recording_sid={rec_sid}")
                                        db_conn2 = get_db()
                                        if db_conn2 is not None:
                                            await asyncio.to_thread(
                                                db_conn2.calls.update_one,
                                                {"call_sid": local_call_sid},
                                                {"$set": {"recording_sid": rec_sid, "updated_at": datetime.now(timezone.utc)}},
                                            )
                                    except Exception as e:
                                        logger.error(f"[TWILIO] Failed to start recording for {twilio_call_sid}: {e}")

                                asyncio.create_task(_start_recording_bg(real_call_sid, call_sid))
                            except Exception as e:
                                logger.error(f"[TWILIO] Error scheduling recording start: {e}")
                        
                        # Stream started - Gemini should already be speaking due to greeting
                        logger.info(f"[TWILIO] Stream started, Gemini should be speaking")
                    
                    elif event == "media":
                        # Wait for dynamic Gemini Live Session initialization to complete
                        if not gemini_session_ready.is_set():
                            _debug_write("[TWILIO] Media event received before Gemini session was ready, waiting...")
                            await gemini_session_ready.wait()
                            
                        # Decode base64 audio from Twilio
                        twilio_audio = base64_decode_audio(
                            data["media"]["payload"]
                        )
                        
                        # Convert to Gemini format (µ-law 8kHz → PCM 16kHz)
                        gemini_audio = twilio_to_gemini_audio(twilio_audio)
                        
                        # Send audio directly to Gemini (with reconnection handling)
                        logger.debug(f"[TWILIO] Received user media: {len(twilio_audio)} bytes (raw), {len(gemini_audio)} bytes (converted)")
                        _debug_write(f"[TWILIO] Sending {len(gemini_audio)} bytes PCM to Gemini...")
                        try:
                            await gemini_session.send_audio(gemini_audio)
                            consecutive_send_errors = 0
                            _debug_write(f"[TWILIO] Sent to Gemini successfully")
                        except Exception as send_err:
                            consecutive_send_errors += 1
                            err_str = str(send_err)
                            
                            # Only log first occurrence to prevent log flooding
                            if consecutive_send_errors == 1:
                                logger.error(f"[TWILIO] Gemini send failed: {err_str}")
                            
                            # Detect keepalive timeout = dead session
                            if "keepalive" in err_str.lower() or "1011" in err_str or "ping timeout" in err_str.lower():
                                logger.warning(f"[TWILIO] Gemini session died (keepalive timeout). Reconnecting...")
                                if await try_reconnect_gemini():
                                    try:
                                        await gemini_session.send_audio(gemini_audio)
                                    except Exception:
                                        pass
                                    continue
                                else:
                                    logger.error("[TWILIO] Reconnection failed, ending audio processing")
                                    break
                            
                            if consecutive_send_errors >= MAX_SEND_ERRORS:
                                logger.error(f"[TWILIO] {MAX_SEND_ERRORS} consecutive send failures, breaking")
                                break
                            continue
                    
                    elif event == "stop":
                        logger.info(f"[TWILIO] Stream {stream_sid or call_sid} stopped by Twilio")
                        _debug_write(f"[TWILIO] Stream stopped")
                        break
                        
                except WebSocketDisconnect:
                    _debug_write(f"[TWILIO] WebSocket disconnected")
                    break
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.error(f"Error processing Twilio message: {e}")
                    _debug_write(f"[TWILIO] Error: {str(e)}")
                    continue

        # Task 2: Receive audio from Gemini, convert, send to Twilio
        async def receive_from_gemini():
            """Receive audio from Gemini, convert, and send to Twilio."""
            nonlocal pending_hangup
            nonlocal call_properties
            nonlocal gemini_session_ready
            
            _debug_write(f"[TASK] receive_from_gemini() waiting for session to be ready...")
            logger.info(f"[TASK] receive_from_gemini() waiting for session to be ready...")
            await gemini_session_ready.wait()
            
            _debug_write(f"[TASK] receive_from_gemini() started")
            logger.info(f"[TASK] receive_from_gemini() started")
            
            audio_count = 0
            try:
                _debug_write(f"[GEMINI] Starting to receive audio from Gemini...")
                async for msg_type, data in gemini_session.receive_audio():
                    _debug_write(f"[GEMINI] Received message type: {msg_type}")
                    
                    if msg_type == "audio":
                        audio_count += 1
                        _debug_write(f"[GEMINI] Audio chunk #{audio_count}: {len(data)} bytes raw")
                        twilio_audio = gemini_to_twilio_audio(data)
                        _debug_write(f"[GEMINI] Converted to {len(twilio_audio)} bytes for Twilio")
                        payload = base64_encode_audio(twilio_audio)
                        message = json.dumps({
                            "event": "media",
                            "streamSid": stream_sid or call_sid,
                            "media": {"payload": payload},
                        })
                        try:
                            logger.debug(f"[GEMINI->TWILIO] Sending audio chunk #{audio_count}: streamSid={stream_sid or call_sid} size={len(payload)} chars")
                            await websocket.send_text(message)
                            _debug_write(f"[GEMINI] Sent audio chunk #{audio_count} to Twilio")
                            logger.info(f"[GEMINI] Sent audio chunk #{audio_count} to Twilio (streamSid={stream_sid or call_sid})")
                        except Exception as e:
                            import traceback
                            tb = traceback.format_exc()
                            logger.error(f"Error sending audio to Twilio (chunk #{audio_count}): {e}\n{tb}")
                            _debug_write(f"[GEMINI] Error sending to Twilio: {e}")
                            # Do not return immediately; attempt to continue sending further chunks
                            continue

                    elif msg_type == "transcript":
                        logger.info(f"AI Transcript: {data}")
                        db_conn = get_db()
                        if db_conn is not None and call_sid:
                            try:
                                await asyncio.to_thread(
                                    db_conn.calls.update_one,
                                    {"call_sid": call_sid},
                                    {"$push": {
                                        "transcripts": {
                                            "speaker": "AI",
                                            "text": data,
                                            "timestamp": datetime.now(timezone.utc)
                                        }
                                    }}
                                )
                                logger.info(f"[DB] SUCCESS: Logged AI transcript to 'calls' for {call_sid}")
                            except Exception as e:
                                logger.error(f"[DB] ERROR: Failed to log AI transcript: {str(e)}")

                    elif msg_type == "user_transcript":
                        logger.info(f"User Transcript: {data}")
                        db_conn = get_db()
                        if db_conn is not None and call_sid:
                            try:
                                await asyncio.to_thread(
                                    db_conn.calls.update_one,
                                    {"call_sid": call_sid},
                                    {"$push": {
                                        "transcripts": {
                                            "speaker": "User",
                                            "text": data,
                                            "timestamp": datetime.now(timezone.utc)
                                        }
                                    }}
                                )
                                logger.info(f"[DB] SUCCESS: Logged User transcript to 'calls' for {call_sid}")
                            except Exception as e:
                                logger.error(f"[DB] ERROR: Failed to log User transcript: {str(e)}")

                    elif msg_type == "interrupted":
                        logger.info("AI interrupted by user!")
                        _clear_sid = stream_sid if stream_sid else call_sid
                        if _clear_sid:
                            await websocket.send_text(json.dumps({
                                "event": "clear",
                                "streamSid": _clear_sid
                            }))

                    elif msg_type == "tool_call":
                        # Gemini wants to call a function (e.g., save_lead)
                        logger.info(f"[GEMINI] Received tool_call message type")
                        tool_call_obj = data
                        function_responses = []
                        
                        # Process tool calls quickly to minimize audio lag
                        # Use concurrent execution for tool calls
                        async def process_tool_call(fc):
                            """Process a single tool call and return the response."""
                            nonlocal pending_hangup, captured_interest, captured_budget, captured_city, call_properties, call_appointment
                            logger.info(f"[TOOL] Gemini called '{fc.name}' with args: {fc.args}")
                            
                            if fc.name == "save_lead":
                                try:
                                    db_conn = get_db()
                                    if db_conn is not None:
                                        # Use captured state as fallbacks if args are empty
                                        interest = fc.args.get("interest", "") or captured_interest
                                        budget = fc.args.get("budget", "") or captured_budget
                                        
                                        # Determine city
                                        city = captured_city
                                        if not city and interest:
                                            from csv_logger import extract_city_from_interest
                                            city = extract_city_from_interest(interest)
                                        
                                        lead_doc = {
                                            "name": fc.args.get("name", "Unknown"),
                                            "phone": fc.args.get("phone", ""),
                                            "email": fc.args.get("email", ""),
                                            "city": city,
                                            "interest": interest,
                                            "budget": budget,
                                            "notes": fc.args.get("notes", ""),
                                            "source": "voice_call",
                                            "call_sid": real_call_sid if real_call_sid else call_sid,
                                            "created_at": datetime.now(timezone.utc),
                                            "updated_at": datetime.now(timezone.utc),
                                        }
                                        # Use update_one with upsert=True to prevent duplicates and enrich data
                                        # Consolidate keys to prevent duplicate cards
                                        lead_doc["contactName"] = lead_doc.get("name", "Unknown")
                                        lead_doc["callSid"] = lead_doc.get("call_sid")
                                        lead_doc["callId"] = lead_doc.get("call_sid")
                                        await _save_or_update_contact(db_conn, fc.args.get("phone", ""), lead_doc)
                                        await asyncio.to_thread(
                                            db_conn.calls.update_one,
                                            {"call_sid": call_sid},
                                            {"$set": {
                                                "customer_name": lead_doc["name"],
                                                "customer_phone": lead_doc["phone"],
                                                "twilio_call_sid": real_call_sid if real_call_sid else None,
                                                "updated_at": datetime.now(timezone.utc),
                                            }},
                                        )
                                        logger.info(f"[TOOL] SUCCESS: Lead saved/updated for {lead_doc['name']}")
                                        
                                        # NEW: Push to Zoho CRM in background
                                        asyncio.create_task(push_lead_to_zoho(lead_doc))
                                        
                                        # NEW: Log to CSV
                                        asyncio.create_task(
                                            asyncio.to_thread(log_lead_to_csv, lead_doc, "voice")
                                        )
                                        
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": f"Lead saved successfully for {fc.args.get('name', 'customer')}"}
                                        }
                                    else:
                                        logger.error(f"[TOOL] ERROR: No DB connection for save_lead")
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": "Lead noted internally (database unavailable)"}
                                        }
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR: save_lead failed: {str(e)}")
                                    return {
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": "Lead noted internally"}
                                    }
                            elif fc.name == "request_whatsapp_summary":
                                wa_number = fc.args.get("whatsapp_number", "")
                                permission = fc.args.get("permission_granted", False)
                                send_properties = fc.args.get("send_properties", True)
                                
                                logger.info(f"[TOOL] request_whatsapp_summary called: number={wa_number}, permission={permission}, send_properties={send_properties}")
                                
                                if not permission:
                                    logger.info(f"[TOOL] WhatsApp permission not granted")
                                    return {
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": "Okay, no problem. Thank you for your time!"}
                                    }
                                elif wa_number:
                                    _wa_number = wa_number
                                    _lead_name = fc.args.get("name") or fc.args.get("customer_name") or "Valued Customer"
                                    _lookup_sid = real_call_sid if real_call_sid else call_sid
                                    _props = list(call_properties)
                                    _appointment = dict(call_appointment) if call_appointment else None
                                    _search_terms = [t for t in [captured_interest, captured_budget] if t]

                                    _db = get_db()
                                    _transcripts = []
                                    _resolved_props = list(_props)

                                    try:
                                        if _db is not None:
                                            _call_doc = await asyncio.to_thread(_db.calls.find_one, _call_lookup_filter(_lookup_sid))
                                            if _call_doc and "transcripts" in _call_doc:
                                                _transcripts = _call_doc["transcripts"]
                                            if send_properties and not _resolved_props:
                                                _resolved_props = await find_properties_for_whatsapp(_db, _search_terms)

                                        _result = await asyncio.wait_for(
                                            send_whatsapp_summary_and_properties(
                                                phone=_wa_number,
                                                name=_lead_name,
                                                call_sid=_lookup_sid,
                                                transcripts=_transcripts,
                                                properties=_resolved_props if send_properties else None,
                                                appointment_details=_appointment,
                                            ),
                                            timeout=15.0
                                        )

                                        _summary_text = _result.get("summary", {}).get("summaryText", "") if isinstance(_result, dict) else ""
                                        if _db is not None and _lookup_sid and _summary_text:
                                            await asyncio.to_thread(
                                                _db.calls.update_one,
                                                _call_lookup_filter(_lookup_sid),
                                                {"$set": {
                                                    "summary": _summary_text,
                                                    "updated_at": datetime.now(timezone.utc),
                                                }},
                                            )

                                        logger.info(f"[TOOL] WhatsApp send result for {wa_number}: {json.dumps(_result, default=str)}")
                                    except asyncio.TimeoutError:
                                        logger.warning(f"[TOOL] request_whatsapp_summary timed out for {wa_number}")
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": f"I couldn't finish sending WhatsApp to {wa_number} in time."}
                                        }
                                    except Exception as e:
                                        logger.error(f"[TOOL] request_whatsapp_summary error: {str(e)}")
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": "I couldn't send the WhatsApp message right now."}
                                        }
                                    return {
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": f"I'm sending the call summary to your WhatsApp {wa_number} now. Is there anything else I can help you with?"}
                                    }
                                else:
                                    return {
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": "Could you please provide your WhatsApp number to send the summary?"}
                                    }
                            
                            elif fc.name == "get_properties":
                                location = fc.args.get("location", "")
                                city = fc.args.get("city", "")
                                property_type = fc.args.get("property_type", "")
                                budget_min = fc.args.get("budget_min", "")
                                budget_max = fc.args.get("budget_max", "")
                                state = fc.args.get("state", "")
                                region = fc.args.get("region", "")
                                
                                try:
                                    db_conn = get_db()
                                    if db_conn is not None:
                                        requested_city = city.strip()
                                        resolved_city = await resolve_city_against_db(db_conn, requested_city)
                                        if requested_city and resolved_city and resolved_city.lower() != requested_city.lower():
                                            logger.info(f"[TOOL] City normalized from '{requested_city}' to '{resolved_city}' based on DB cities")
                                        city = resolved_city

                                        # If resolved_city contains a comma, assume it encodes 'City, State'
                                        # and split into `city` and `state` to satisfy the strict prompt rule.
                                        if city and "," in city:
                                            parts = [p.strip() for p in city.split(",") if p.strip()]
                                            if parts:
                                                city = parts[0]
                                                if len(parts) > 1 and not state:
                                                    state = parts[1]
                                                    logger.info(f"[TOOL] Extracted state='{state}' from resolved city string")
                                        else:
                                            # As a fallback, try to infer state from a matching property document
                                            try:
                                                sample_prop = await asyncio.to_thread(
                                                    lambda: db_conn.properties.find_one({"city": {"$regex": requested_city, "$options": "i"}})
                                                )
                                                if sample_prop:
                                                    prop_city = sample_prop.get("city", "")
                                                    if "," in prop_city and not state:
                                                        parts = [p.strip() for p in prop_city.split(",") if p.strip()]
                                                        if parts and len(parts) > 1:
                                                            city = parts[0]
                                                            state = parts[1]
                                                            logger.info(f"[TOOL] Inferred state='{state}' from sample property city field")
                                            except Exception:
                                                pass

                                        # Parse budget values to numbers for comparison
                                        parsed_budget_min = parse_budget_to_number(budget_min) if budget_min else None
                                        parsed_budget_max = parse_budget_to_number(budget_max) if budget_max else None
                                        
                                        # Build the search query with flexible matching
                                        def build_query(loc=None, cit=None, ptype=None, include_project_name=True):
                                            """Build MongoDB query with flexible matching."""
                                            import re
                                            query = {}
                                            
                                            # Property type matching (flexible: 2BHK matches "2 BHK", "2BHK", etc.)
                                            if ptype:
                                                match = re.search(r'(\d)\s*BHK', ptype, re.I)
                                                if match:
                                                    type_regex = f"{match.group(1)}\\s*BHK"
                                                    query["type"] = {"$regex": type_regex, "$options": "i"}
                                                elif ptype.strip().lower() in ["residential", "residential property", "home", "house", "apartment"]:
                                                    query["type"] = {"$regex": r"(bhk|villa|flat|residency|apartment|house|home)", "$options": "i"}
                                                else:
                                                    query["type"] = {"$regex": ptype.strip(), "$options": "i"}
                                            
                                            # City matching (Strict)
                                            if cit:
                                                query["city"] = {"$regex": cit.strip(), "$options": "i"}
                                            
                                            # Location/Project matching (Flexible OR)
                                            if loc:
                                                loc_regex = loc.strip()
                                                or_clauses = [
                                                    {"location": {"$regex": loc_regex, "$options": "i"}},
                                                ]
                                                if include_project_name:
                                                    or_clauses.append({"project_name": {"$regex": loc_regex, "$options": "i"}})
                                                
                                                # If city wasn't provided, allow location to match city field too
                                                if not cit:
                                                    or_clauses.append({"city": {"$regex": loc_regex, "$options": "i"}})
                                                
                                                query["$or"] = or_clauses
                                            
                                            # Budget filtering using price_value field
                                            if parsed_budget_min is not None or parsed_budget_max is not None:
                                                query["price_value"] = {}
                                                if parsed_budget_min is not None:
                                                    query["price_value"]["$gte"] = parsed_budget_min
                                                if parsed_budget_max is not None:
                                                    query["price_value"]["$lte"] = parsed_budget_max
                                            
                                            return query
                                        
                                        # Try strict search first (location + city + type + budget)
                                        query = build_query(loc=location, cit=city, ptype=property_type)
                                        
                                        properties_cursor = await asyncio.to_thread(
                                            lambda: list(db_conn.properties.find(query).limit(5))
                                        )
                                        properties = list(properties_cursor)
                                        
                                        # Fallback 1: If no results, try broader search (loc/cit + type, no budget)
                                        if not properties and (location or city):
                                            logger.info("[TOOL] Strict search returned no results, trying broader search (no budget)...")
                                            # We use 'location or city' as loc to be broad
                                            query_fallback = build_query(loc=location or city, ptype=property_type)
                                            # Explicitly remove budget filter if it was there
                                            if "price_value" in query_fallback:
                                                del query_fallback["price_value"]
                                            
                                            properties_cursor = await asyncio.to_thread(
                                                lambda: list(db_conn.properties.find(query_fallback).limit(5))
                                            )
                                            properties = list(properties_cursor)
                                        
                                        # Fallback 1.5: If still no results, try location/city-only search
                                        if not properties and (location or city):
                                            logger.info("[TOOL] Broader search returned no results, trying location/city-only search...")
                                            query_loc_only = build_query(loc=location or city)
                                            # Explicitly remove type and budget filters
                                            if "type" in query_loc_only:
                                                del query_loc_only["type"]
                                            if "price_value" in query_loc_only:
                                                del query_loc_only["price_value"]
                                            
                                            properties_cursor = await asyncio.to_thread(
                                                lambda: list(db_conn.properties.find(query_loc_only).limit(5))
                                            )
                                            properties = list(properties_cursor)
                                        
                                        # Fallback 2: If still no results, try just type search
                                        # only when no city/location was provided. This avoids
                                        # drifting into results from another city.
                                        if not properties and property_type and not (location or city):
                                            logger.info("[TOOL] Broader search returned no results, trying type-only search...")
                                            import re
                                            match = re.search(r'(\d)\s*BHK', property_type, re.I)
                                            type_regex = f"{match.group(1)}\\s*BHK" if match else property_type
                                            query_type_only = {"type": {"$regex": type_regex, "$options": "i"}}
                                            
                                            properties_cursor = await asyncio.to_thread(
                                                lambda: list(db_conn.properties.find(query_type_only).limit(5))
                                            )
                                            properties = list(properties_cursor)
                                        
                                        if properties:
                                            formatted_props = []
                                            for prop in properties:
                                                formatted_props.append(
                                                    f"• {prop.get('project_name', 'Unknown')} - {prop.get('type', 'N/A')} "
                                                    f"in {prop.get('location', 'N/A')}, {prop.get('city', 'N/A')} "
                                                    f"at {prop.get('price', 'N/A')}."
                                                )
                                            response_text = "\n".join(formatted_props)
                                            
                                            # Log search results for debugging
                                            logger.info(f"[TOOL] get_properties found {len(properties)} properties. args: city={city}, state={state}, region={region}, location={location}, type={property_type}")
                                            if parsed_budget_min is not None or parsed_budget_max is not None:
                                                logger.info(f"[TOOL] Budget filter: min={parsed_budget_min}, max={parsed_budget_max}")
                                        else:
                                            response_text = "No matching properties found."
                                            logger.info(f"[TOOL] No properties found after all search attempts. args: city={city}, state={state}, region={region}, location={location}, type={property_type}")
                                        
                                        # Store properties for WhatsApp later
                                        call_properties = properties
                                        
                                        # Capture interest and budget from the search criteria
                                        interest_parts = []
                                        if property_type:
                                            interest_parts.append(property_type)
                                        if location:
                                            interest_parts.append(location)
                                        elif city:
                                            interest_parts.append(city)
                                        if state:
                                            interest_parts.append(state)
                                        captured_interest = " ".join(interest_parts) if interest_parts else ""

                                        # Capture budget correctly from min/max
                                        budget_parts = []
                                        if budget_min:
                                            budget_parts.append(f"from {budget_min}")
                                        if budget_max:
                                            budget_parts.append(f"up to {budget_max}")
                                        if budget_parts:
                                            captured_budget = " ".join(budget_parts)
                                        
                                        # Capture city
                                        if city:
                                            captured_city = city
                                        elif location:
                                            captured_city = location
                                        
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": response_text}
                                        }
                                    else:
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": "I apologize, but I'm unable to check property availability right now."}
                                        }
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR: get_properties failed: {str(e)}")
                                    import traceback
                                    logger.error(f"[TOOL] ERROR: get_properties traceback: {traceback.format_exc()}")
                                    return {
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": "I apologize, but there was an error retrieving property information."}
                                    }

                            elif fc.name == "check_appointment_availability":
                                date = fc.args.get("date", "")
                                property_name = fc.args.get("property_name", "")

                                try:
                                    db_conn = get_db()
                                    if db_conn is not None and date:
                                        is_valid_date, date_msg = validate_preferred_date_future_only(date)
                                        if not is_valid_date:
                                            return {
                                                "name": fc.name,
                                                "id": fc.id,
                                                "response": {"result": date_msg}
                                            }

                                        query = {"preferred_date": date, "status": {"$in": ["scheduled", "confirmed"]}}
                                        if property_name:
                                            query["property_of_interest"] = {"$regex": property_name, "$options": "i"}

                                        existing_count = await asyncio.to_thread(
                                            lambda: db_conn.pending_calls.count_documents(query)
                                        )

                                        if existing_count > 0:
                                            result_text = f"There are already {existing_count} appointments on {date}."
                                        else:
                                            result_text = f"{date} looks available for booking."

                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": result_text}
                                        }
                                    else:
                                        return {
                                            "name": fc.name,
                                            "id": fc.id,
                                            "response": {"result": "I need a date to check availability."}
                                        }
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR: check_appointment_availability failed: {str(e)}")
                                    return {
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": "I couldn't check availability right now."}
                                    }
                            
                            
                            elif fc.name == "book_appointment":
                                customer_name = fc.args.get("customer_name", "Unknown")
                                customer_phone = fc.args.get("customer_phone", "")
                                appointment_type = fc.args.get("appointment_type", "site_visit")
                                property_interest = fc.args.get("property_of_interest", "")
                                preferred_date = fc.args.get("preferred_date", "")
                                preferred_time = fc.args.get("preferred_time", "")
                                notes = fc.args.get("notes", "")
                                
                                try:
                                    db_conn = get_db()
                                    if db_conn is not None:
                                        is_valid_date, date_msg = validate_preferred_date_future_only(preferred_date)
                                        if not is_valid_date:
                                            return {
                                                "name": fc.name,
                                                "id": fc.id,
                                                "response": {"result": date_msg}
                                            }

                                        date_check = {"preferred_date": preferred_date, "status": {"$in": ["scheduled", "confirmed"]}}
                                        existing_count = await asyncio.to_thread(
                                            lambda: db_conn.pending_calls.count_documents(date_check)
                                        )
                                        
                                        if existing_count >= Config.MAX_APPOINTMENTS_PER_DAY:
                                            return {"name": fc.name, "id": fc.id, "response": {"result": f"Sorry, we've reached our maximum limit of {Config.MAX_APPOINTMENTS_PER_DAY} appointments for {preferred_date}."}}
                                        
                                        appointment_doc = {
                                            "customer_name": customer_name,
                                            "customer_phone": customer_phone,
                                            "appointment_type": appointment_type,
                                            "property_of_interest": property_interest,
                                            "preferred_date": preferred_date,
                                            "preferred_time": preferred_time,
                                            "notes": notes,
                                            "status": "scheduled",
                                            "call_sid": real_call_sid if real_call_sid else call_sid,
                                            "created_at": datetime.now(timezone.utc),
                                        }
                                        call_appointment = appointment_doc
                                        result = await asyncio.to_thread(
                                            db_conn.pending_calls.insert_one, appointment_doc
                                        )
                                        logger.info(f"[TOOL] SUCCESS: Appointment booked with ID: {result.inserted_id}")
                                        
                                        # Send WhatsApp confirmation in background
                                        wa_number = fc.args.get("whatsapp_number", "")
                                        if wa_number:
                                            _wa_num = wa_number
                                            _cust_name = customer_name
                                            _apt_type = appointment_type
                                            _apt_date = preferred_date
                                            _apt_time = preferred_time
                                            _prop = property_interest
                                            async def _send_wa_confirm():
                                                try:
                                                    return await send_whatsapp(
                                                        to_number=_wa_num,
                                                        body=f"Hi {_cust_name}! Your {_apt_type.replace('_', ' ')} is booked.\nDate: {_apt_date}\nTime: {_apt_time}\nProperty: {_prop or 'To be confirmed'}"
                                                    )
                                                except Exception as e:
                                                    logger.error(f"[TOOL] WhatsApp send error: {str(e)}")
                                            asyncio.create_task(_send_wa_confirm())
                                        
                                        return {
                                            "name": fc.name, "id": fc.id,
                                            "response": {"result": f"Your {appointment_type.replace('_', ' ')} is booked for {preferred_date} at {preferred_time}."}
                                        }
                                    else:
                                        return {"name": fc.name, "id": fc.id, "response": {"result": "I apologize, but I'm unable to book appointments right now."}}
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR: book_appointment failed: {str(e)}")
                                    return {"name": fc.name, "id": fc.id, "response": {"result": "I apologize, but there was an error booking your appointment."}}
                            
                            elif fc.name == "end_call":
                                reason = fc.args.get("reason", "conversation_complete")
                                logger.info(f"[TOOL] end_call requested with reason: {reason}")
                                pending_hangup = True
                                return {
                                    "name": fc.name, "id": fc.id,
                                    "response": {"result": "Okay, main ab call end kar rahi hoon. Baat karne ke liye dhanyawad!"}
                                }
                            
                            elif fc.name == "send_appointment_reminder":
                                phone = fc.args.get("phone", "")
                                name = fc.args.get("name", "")
                                apt_type = fc.args.get("appointment_type", "site_visit")
                                apt_date = fc.args.get("appointment_date", "")
                                apt_time = fc.args.get("appointment_time", "")
                                prop_name = fc.args.get("property_name", "")
                                
                                if not phone:
                                    return {"name": fc.name, "id": fc.id, "response": {"result": "I need your phone number to send the reminder."}}
                                
                                _phone = phone
                                _name = name
                                _apt_type = apt_type
                                _apt_date = apt_date
                                _apt_time = apt_time
                                _prop_name = prop_name
                                async def _send_reminder_bg():
                                    try:
                                        return await asyncio.wait_for(send_appointment_reminder(_phone, _name, _apt_type, _apt_date, _apt_time, _prop_name), timeout=10.0)
                                    except asyncio.TimeoutError:
                                        return {"success": False, "error": "timeout"}
                                    except Exception as e:
                                        return {"success": False, "error": str(e)}
                                asyncio.create_task(_send_reminder_bg())
                                return {"name": fc.name, "id": fc.id, "response": {"result": f"I'm sending the appointment reminder to {name} now."}}
                            
                            elif fc.name == "send_property_pack":
                                phone = fc.args.get("phone", "")
                                name = fc.args.get("name", "")
                                property_ids = fc.args.get("property_ids", [])
                                
                                if not phone:
                                    return {"name": fc.name, "id": fc.id, "response": {"result": "I need your phone number to send the property pack."}}
                                elif not property_ids:
                                    return {"name": fc.name, "id": fc.id, "response": {"result": "I don't have any properties to send right now."}}
                                
                                _phone = phone
                                _name = name
                                _prop_ids = property_ids
                                async def _send_property_pack_bg():
                                    try:
                                        db_conn = get_db()
                                        if db_conn is not None:
                                            properties = []
                                            for prop_id in _prop_ids[:5]:
                                                prop = await asyncio.to_thread(db_conn.properties.find_one, {"project_name": {"$regex": prop_id, "$options": "i"}})
                                                if prop:
                                                    prop["_id"] = str(prop["_id"])
                                                    properties.append(prop)
                                            return await asyncio.wait_for(send_property_pack(_phone, _name, properties), timeout=10.0)
                                        return {"success": False, "error": "No DB"}
                                    except asyncio.TimeoutError:
                                        return {"success": False, "error": "timeout"}
                                    except Exception as e:
                                        return {"success": False, "error": str(e)}
                                asyncio.create_task(_send_property_pack_bg())
                                return {"name": fc.name, "id": fc.id, "response": {"result": f"I'm sending the property pack to {phone} now."}}
                            
                            elif fc.name == "send_followup_survey":
                                phone = fc.args.get("phone", "")
                                name = fc.args.get("name", "")
                                call_sid_val = fc.args.get("call_sid", "")
                                
                                if not phone:
                                    return {"name": fc.name, "id": fc.id, "response": {"result": "I need your phone number to send the survey."}}
                                
                                _phone = phone
                                _name = name
                                _call_sid = call_sid_val
                                async def _send_survey_bg():
                                    try:
                                        return await asyncio.wait_for(send_followup_survey(_phone, _name, _call_sid), timeout=10.0)
                                    except asyncio.TimeoutError:
                                        return {"success": False, "error": "timeout"}
                                    except Exception as e:
                                        return {"success": False, "error": str(e)}
                                asyncio.create_task(_send_survey_bg())
                                return {"name": fc.name, "id": fc.id, "response": {"result": f"I'm sending a quick survey to your WhatsApp."}}
                            
                            elif fc.name == "update_sentiment":
                                # Save customer sentiment to MongoDB contacts collection
                                phone = fc.args.get("phone", "")
                                sentiment = fc.args.get("sentiment", "neutral")
                                sentiment_description = fc.args.get("sentiment_description", "")
                                
                                logger.info(f"[TOOL] update_sentiment called for {phone}: {sentiment}")
                                
                                try:
                                    db_conn = get_db()
                                    if db_conn is not None and phone:
                                        # Upsert sentiment data into contacts collection
                                        sentiment_fields = {
                                            "sentiment": sentiment,
                                            "sentimentDescription": sentiment_description,
                                            "callSid": real_call_sid if real_call_sid else call_sid,
                                            "callId": real_call_sid if real_call_sid else call_sid,
                                        }
                                        await _save_or_update_contact(db_conn, phone, sentiment_fields)
                                        logger.info(f"[TOOL] SUCCESS: Sentiment saved for {phone} -> {sentiment}")
                                        return {"name": fc.name, "id": fc.id, "response": {"result": f"Thank you. I've noted your {sentiment} feedback."}}
                                    else:
                                        return {"name": fc.name, "id": fc.id, "response": {"result": "I've noted your feedback."}}
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR: update_sentiment failed: {str(e)}")
                                    return {"name": fc.name, "id": fc.id, "response": {"result": "Thank you for your feedback."}}
                            
                            else:
                                logger.warning(f"[TOOL] WARNING: Unknown function '{fc.name}'")
                                return {"name": fc.name, "id": fc.id, "response": {"result": "Function not available"}}
                        
                        # Execute the tool call and collect responses
                        if tool_call_obj and hasattr(tool_call_obj, 'function_calls'):
                            for fc in tool_call_obj.function_calls:
                                try:
                                    response = await process_tool_call(fc)
                                    function_responses.append(response)
                                    logger.info(f"[TOOL] Executed '{fc.name}' successfully")
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR executing '{fc.name}': {str(e)}")
                                    function_responses.append({
                                        "name": fc.name,
                                        "id": fc.id,
                                        "response": {"result": f"Error executing tool: {str(e)}"}
                                    })
                        
                        # Send tool response back to Gemini
                        if function_responses:
                            try:
                                await gemini_session.send_tool_response(function_responses)
                                logger.info(f"[TOOL] Tool response sent back to Gemini")
                            except Exception as e:
                                logger.error(f"[TOOL] ERROR sending tool response: {str(e)}")

                    elif msg_type == "turn_complete":
                        logger.info("AI turn complete -- staying alive")
                        # If end_call was requested, now hang up (AI finished speaking)
                        if pending_hangup:
                            logger.info("[TOOL] AI finished goodbye. Initiating hangup...")
                            # Small grace period for final audio frames to be delivered
                            await asyncio.sleep(0.5)
                            if real_call_sid:
                                try:
                                    def _hangup():
                                        client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                                        client.calls(real_call_sid).update(status="completed")
                                    await asyncio.to_thread(_hangup)
                                    logger.info(f"[TOOL] SUCCESS: Call {real_call_sid} terminated by agent")
                                except Exception as e:
                                    logger.error(f"[TOOL] ERROR: Failed to hangup call: {str(e)}")
                            else:
                                logger.warning(f"[TOOL] WARNING: No real_call_sid available for hangup")
            except Exception as e:
                logger.error(f"receive_from_gemini error: {e}")

        # Run both tasks concurrently (matching old working pattern)
        _debug_write(f"[INIT] Starting both tasks concurrently...")
        logger.info(f"[INIT] Starting both tasks concurrently...")
        results = await asyncio.gather(
            receive_from_twilio(),
            receive_from_gemini(),
            return_exceptions=True,
        )
        _debug_write(f"[INIT] Both tasks completed, {len(results)} results")
        logger.info(f"[INIT] Both tasks completed, {len(results)} results")
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[INIT] Task {i} failed: {result}")
            else:
                logger.info(f"[INIT] Task {i} completed normally")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {call_sid}")
    except Exception as e:
        logger.error(f"Error in WebSocket {call_sid}: {e}")
    finally:
        # Cleanup - use real_call_sid if available for DB operations
        _cleanup_sid = real_call_sid if real_call_sid else call_sid
        if call_sid:
            active_streams.pop(call_sid, None)
            # Close Gemini session with error handling to prevent flood of errors
            try:
                await gemini_manager.close_session(call_sid)
            except Exception as e:
                logger.warning(f"[CLEANUP] Error closing Gemini session for {call_sid}: {e}")
            call_sessions.pop(call_sid, None)

            # Update call record in MongoDB
            db = get_db()
            if db is not None and _cleanup_sid:
                ended_at = datetime.now(timezone.utc)
                call_doc = await asyncio.to_thread(
                    db.calls.find_one, _call_lookup_filter(_cleanup_sid)
                )
                duration_seconds = 0
                if call_doc and call_doc.get("started_at"):
                    started_at = call_doc["started_at"]
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    duration_seconds = int((ended_at - started_at).total_seconds())

                await asyncio.to_thread(
                    db.calls.update_one,
                    _call_lookup_filter(_cleanup_sid),
                    {"$set": {
                        "status": "completed",
                        "ended_at": ended_at,
                        "duration_seconds": duration_seconds,
                    }},
                )

                # ─── Automatic Sentiment Analysis ─────────────────────────
                # Fetch transcripts from the call document and analyze sentiment
                try:
                    call_doc = await asyncio.to_thread(
                        db.calls.find_one, _call_lookup_filter(_cleanup_sid)
                    )
                    if call_doc and call_doc.get("transcripts"):
                        transcripts = call_doc["transcripts"]
                        # Use the captured phone number from the call
                        phone_number = customer_phone if customer_phone else ""
                        
                        # If no phone number from Twilio, try to extract from transcripts
                        if not phone_number:
                            for turn in reversed(transcripts):
                                if turn.get("speaker") in ("User", "user") and turn.get("text", "").strip():
                                    # Try to extract phone from user's last message
                                    import re
                                    phone_match = re.search(r'[\+]?91?[\s\-]?(\d{5})[\s\-]?(\d{5})', turn.get("text", ""))
                                    if phone_match:
                                        phone_number = f"+91{phone_match.group(1)}{phone_match.group(2)}"
                                        logger.info(f"[SENTIMENT] Extracted phone from transcript: {phone_number}")
                                        break
                        
                        if phone_number and len(transcripts) > 0:
                            logger.info(f"[SENTIMENT] Running AI sentiment analysis for {phone_number}...")
                            
                            # Run sentiment analysis in background to avoid blocking
                            async def _analyze_and_save_sentiment():
                                try:
                                    result = await analyze_sentiment_from_transcript(
                                        phone=phone_number,
                                        transcripts=transcripts,
                                    )
                                    sentiment = result.get("sentiment", "neutral")
                                    sentiment_desc = result.get("sentiment_description", "")
                                    
                                    logger.info(f"[SENTIMENT] AI analysis for {phone_number}: {sentiment} - {sentiment_desc}")
                                    
                                    # Save sentiment to contacts collection
                                    if db is not None:
                                        sentiment_fields = {
                                            "sentiment": sentiment,
                                            "sentimentDescription": sentiment_desc,
                                            "sentimentSource": "ai_auto_analysis",
                                            "callSid": _cleanup_sid,
                                            "callId": _cleanup_sid,
                                        }
                                        await _save_or_update_contact(db, phone_number, sentiment_fields)
                                        await asyncio.to_thread(
                                            db.calls.update_one,
                                            _call_lookup_filter(_cleanup_sid),
                                            {"$set": {
                                                "sentiment": sentiment,
                                                "sentiment_description": sentiment_desc,
                                                "twilio_call_sid": real_call_sid if real_call_sid else None,
                                                "updated_at": datetime.now(timezone.utc),
                                            }},
                                        )
                                        logger.info(f"[SENTIMENT] SUCCESS: Saved sentiment for {phone_number} -> {sentiment}")
                                    else:
                                        logger.warning(f"[SENTIMENT] No DB connection to save sentiment for {phone_number}")
                                except Exception as e:
                                    logger.error(f"[SENTIMENT] ERROR: Failed to analyze/save sentiment: {str(e)}")
                            
                            # Fire and forget - don't block cleanup
                            asyncio.create_task(_analyze_and_save_sentiment())
                        else:
                            logger.info(f"[SENTIMENT] Skipping analysis: phone={phone_number}, transcripts={len(transcripts)}")
                    else:
                        logger.info(f"[SENTIMENT] No transcripts found for {_cleanup_sid}")
                except Exception as e:
                    logger.error(f"[SENTIMENT] ERROR: Failed to trigger sentiment analysis: {str(e)}")

            logger.info(f"Cleanup complete for {_cleanup_sid}")


# ─── Dashboard REST API Endpoints ─────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    _debug_write("[HEALTH] Health check requested")
    db_status = "connected"
    try:
        # Run db check in thread
        db = await asyncio.to_thread(get_db)
        if db is None:
            db_status = "disconnected"
    except Exception as e:
        db_status = "error"
        logger.error(f"DB health check error: {e}")
    
    return {
        "status": "ok",
        "gemini_sessions": gemini_manager.active_count,
        "active_calls": len(active_streams),
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/debug/routes")
async def debug_routes():
    """Debug endpoint to list all registered routes."""
    _debug_write("[DEBUG] Route listing requested")
    routes = []
    for route in app.routes:
        routes.append({
            "path": route.path,
            "name": route.name,
            "type": type(route).__name__,
        })
    return {
        "total_routes": len(routes),
        "routes": routes,
    }


# ─── WhatsApp REST API Endpoints ──────────────────────────────────────

@app.post("/api/whatsapp/appointment-reminder")
async def trigger_appointment_reminder(
    request: Request,
    auth: bool = Depends(verify_api_key),
):
    """
    Send an appointment reminder via WhatsApp.
    Request body: {"phone": "+91...", "name": "...", "appointment_type": "site_visit", "appointment_date": "2026-04-25", "appointment_time": "14:30", "property_name": "..."}
    """
    body = await request.json()
    phone = body.get("phone")
    name = body.get("name", "")
    appointment_type = body.get("appointment_type", "site_visit")
    appointment_date = body.get("appointment_date", "")
    appointment_time = body.get("appointment_time", "")
    property_name = body.get("property_name", "")
    
    if not phone:
        return {"success": False, "message": "Phone number required"}
    
    result = await send_appointment_reminder(phone, name, appointment_type, appointment_date, appointment_time, property_name)
    
    if result.get("success"):
        logger.info(f"[WHATSAPP] Appointment reminder sent to {phone}")
        return {"success": True, "message": "Appointment reminder sent"}
    else:
        logger.error(f"[WHATSAPP] Failed to send appointment reminder: {result.get('error')}")
        return {"success": False, "message": result.get("error", "Failed to send")}


@app.post("/api/whatsapp/property-pack")
async def trigger_property_pack(
    request: Request,
    auth: bool = Depends(verify_api_key),
):
    """
    Send a curated property pack via WhatsApp.
    Request body: {"phone": "+91...", "name": "...", "properties": [...]}
    """
    body = await request.json()
    phone = body.get("phone")
    name = body.get("name", "")
    properties = body.get("properties", [])
    
    if not phone:
        return {"success": False, "message": "Phone number required"}
    
    result = await send_property_pack(phone, name, properties)
    
    if result.get("success"):
        logger.info(f"[WHATSAPP] Property pack sent to {phone}")
        return {"success": True, "message": f"Property pack with {len(properties)} properties sent"}
    else:
        logger.error(f"[WHATSAPP] Failed to send property pack: {result.get('error')}")
        return {"success": False, "message": result.get("error", "Failed to send")}


@app.post("/api/whatsapp/survey")
async def trigger_followup_survey(
    request: Request,
    auth: bool = Depends(verify_api_key),
):
    """
    Send a follow-up satisfaction survey via WhatsApp.
    Request body: {"phone": "+91...", "name": "...", "call_sid": "..."}
    """
    body = await request.json()
    phone = body.get("phone")
    name = body.get("name", "")
    call_sid = body.get("call_sid", "")
    
    if not phone:
        return {"success": False, "message": "Phone number required"}
    
    result = await send_followup_survey(phone, name, call_sid)
    
    if result.get("success"):
        logger.info(f"[WHATSAPP] Follow-up survey sent to {phone}")
        return {"success": True, "message": "Follow-up survey sent"}
    else:
        logger.error(f"[WHATSAPP] Failed to send follow-up survey: {result.get('error')}")
        return {"success": False, "message": result.get("error", "Failed to send")}


@app.get("/api/dashboard-stats")
async def dashboard_stats(auth: bool = Depends(verify_api_key)):
    """
    Get dashboard statistics with async-safe database handling.
    """
    db = await asyncio.to_thread(get_db)
    
    if db is None:
        return {
            "success": True,
            "stats": {
                "totalCalls": 0,
                "ongoingCalls": gemini_manager.active_count,
                "overallSentiment": 0,
                "positiveResponseRate": 0,
                "escalationRecommended": 0,
                "pendingCalls": 0,
                "totalContacts": 0,
                "positiveSentiment": 0,
                "negativeSentiment": 0,
                "neutralSentiment": 0,
            }
        }
    
    # Run all database counts in threads to avoid blocking the event loop
    total_calls = await asyncio.to_thread(db.calls.count_documents, {"status": "completed"})
    pending_calls = await asyncio.to_thread(db.calls.count_documents, {"status": {"$in": ["initiated", "connected"]}})
    total_contacts = await asyncio.to_thread(db.contacts.count_documents, {})
    
    positive_sentiment = await asyncio.to_thread(db.contacts.count_documents, {"sentiment": "positive"})
    negative_sentiment = await asyncio.to_thread(db.contacts.count_documents, {"sentiment": "negative"})
    neutral_sentiment = await asyncio.to_thread(db.contacts.count_documents, {"sentiment": "neutral"})
    
    # Calculate overall sentiment score
    positive_response_rate = (positive_sentiment / total_calls * 100) if total_calls > 0 else 0
    overall_sentiment = round((positive_sentiment / total_contacts * 100) if total_contacts > 0 else 0)
    escalation_recommended = negative_sentiment
    
    return {
        "success": True,
        "stats": {
            "totalCalls": total_calls,
            "ongoingCalls": gemini_manager.active_count,
            "overallSentiment": overall_sentiment,
            "positiveResponseRate": round(positive_response_rate, 1),
            "escalationRecommended": escalation_recommended,
            "pendingCalls": pending_calls,
            "totalContacts": total_contacts,
            "positiveSentiment": positive_sentiment,
            "negativeSentiment": negative_sentiment,
            "neutralSentiment": neutral_sentiment,
        }
    }


@app.get("/api/contacts")
async def get_contacts(search: str = Query(""), auth: bool = Depends(verify_api_key)):
    """
    Get contacts with optional search filter.
    Response format matches what the existing React frontend expects.
    """
    db = get_db()
    
    if db is None:
        return {"success": True, "contacts": []}
    
    query = {
        "$or": [
            {"phoneNumber": {"$exists": True, "$ne": None, "$ne": ""}},
            {"phone": {"$exists": True, "$ne": None, "$ne": ""}}
        ]
    }
    if search:
        escaped_search = re.escape(search)
        query = {
            "$and": [
                {
                    "$or": [
                        {"phoneNumber": {"$exists": True, "$ne": None, "$ne": ""}},
                        {"phone": {"$exists": True, "$ne": None, "$ne": ""}}
                    ]
                },
                {
                    "$or": [
                        {"phoneNumber": {"$regex": escaped_search, "$options": "i"}},
                        {"phone": {"$regex": escaped_search, "$options": "i"}},
                        {"contactName": {"$regex": escaped_search, "$options": "i"}},
                        {"name": {"$regex": escaped_search, "$options": "i"}},
                        {"sentimentDescription": {"$regex": escaped_search, "$options": "i"}},
                    ]
                }
            ]
        }
    
    contacts_cursor = await asyncio.to_thread(
        lambda: list(db.contacts.find(query).sort("updated_at", -1).limit(100))
    )
    contacts = contacts_cursor
    
    # Convert ObjectId to string for JSON serialization
    for contact in contacts:
        contact["_id"] = str(contact["_id"])
    
    return {"success": True, "contacts": contacts}

@app.post("/api/customer-details")
async def save_customer_details(payload: CustomerDetailsRequest, auth: bool = Depends(verify_api_key)):
    """Save or update customer details along with related call metadata."""
    db = get_db()
    if db is None:
        return {"success": False, "message": "Database not available"}

    phone = (payload.phoneNumber or "").strip()
    call_id = (payload.callId or "").strip()
    call_sid = (payload.callSid or "").strip()

    if not phone and not call_id and not call_sid:
        return {"success": False, "message": "phoneNumber or callId/callSid is required"}

    update_doc = {
        "contactName": (payload.contactName or "").strip() or "Unknown",
        "name": (payload.contactName or "").strip() or "Unknown",
        "interest": (payload.interest or "").strip(),
        "sentiment": (payload.sentiment or "neutral").strip() or "neutral",
        "sentimentDescription": (payload.sentimentDescription or "").strip(),
        "notes": (payload.notes or "").strip(),
        "callId": call_id,
        "callSid": call_sid,
        "twilioCallSid": (payload.twilioCallSid or "").strip(),
        "recordingSid": (payload.recordingSid or "").strip(),
        "recordingUrl": (payload.recordingUrl or "").strip(),
        "callDetails": payload.callDetails or {},
        "source": "call_detail_manual_save",
    }

    try:
        # Use our robust helper to avoid duplicate contact cards!
        res = await _save_or_update_contact(db, phone, update_doc)
        return {
            "success": True,
            "message": "Customer details saved",
            "matched": 1 if res else 0,
            "modified": 1 if res else 0,
            "upserted_id": res["id"] if res else None,
        }
    except Exception as e:
        logger.error(f"[API] save_customer_details error: {e}")
        return {"success": False, "message": str(e)}


@app.get("/api/calls")
async def get_calls(auth: bool = Depends(verify_api_key)):
    """Get all call records stored in MongoDB."""
    db = get_db()

    if db is None:
        return {"success": True, "calls": []}

    calls = await asyncio.to_thread(
        lambda: list(db.calls.find({}).sort("started_at", -1).limit(200))
    )

    contacts = await asyncio.to_thread(
        lambda: list(
            db.contacts.find(
                {},
                {
                    "phoneNumber": 1,
                    "phone": 1,
                    "customer_phone": 1,
                    "sentiment": 1,
                    "sentimentDescription": 1,
                    "sentiment_description": 1,
                    "callSid": 1,
                    "callId": 1,
                    "twilioCallSid": 1,
                },
            )
        )
    )
    contact_by_phone, contact_by_call = _build_contact_sentiment_indexes(contacts)

    calls = [_apply_sentiment_fallback(_serialize_call_document(call), contact_by_phone, contact_by_call) for call in calls]

    return {"success": True, "calls": calls}


@app.get("/api/pending-calls")
async def get_pending_calls(auth: bool = Depends(verify_api_key)):
    """Get pending/scheduled calls. Response format matches frontend."""
    db = get_db()
    
    if db is None:
        return {"success": True, "pending_calls": []}
    
    pending = await asyncio.to_thread(
        lambda: list(db.pending_calls.find({"status": "pending"}).sort("created_at", -1).limit(100))
    )
    
    for call in pending:
        call["_id"] = str(call["_id"])
        for field in ["created_at", "updated_at"]:
            if field in call and call[field]:
                call[field] = call[field].isoformat()
    
    return {"success": True, "pending_calls": pending}


@app.get("/api/call/{call_sid}")
async def get_call_detail(call_sid: str, auth: bool = Depends(verify_api_key)):
    """Get a single call document by call SID (twilio_call_sid or internal call_sid)."""
    db = get_db()
    if db is None:
        return {"success": False, "error": "No DB"}

    call_doc = await asyncio.to_thread(lambda: db.calls.find_one({"$or": [{"call_sid": call_sid}, {"twilio_call_sid": call_sid}]}))
    if not call_doc:
        return {"success": False, "error": "Not found"}

    contacts = await asyncio.to_thread(
        lambda: list(
            db.contacts.find(
                {},
                {
                    "phoneNumber": 1,
                    "phone": 1,
                    "customer_phone": 1,
                    "sentiment": 1,
                    "sentimentDescription": 1,
                    "sentiment_description": 1,
                    "callSid": 1,
                    "callId": 1,
                    "twilioCallSid": 1,
                },
            )
        )
    )
    contact_by_phone, contact_by_call = _build_contact_sentiment_indexes(contacts)

    call_doc = _apply_sentiment_fallback(_serialize_call_document(call_doc), contact_by_phone, contact_by_call)
    return {"success": True, "call": call_doc, "callData": call_doc}



@app.post("/api/recording-status")
async def recording_status(request: Request):
    """Twilio will POST recording status here when a recording is available.

    Expected form fields include: RecordingSid, RecordingUrl, CallSid, RecordingDuration
    """
    form = await request.form()
    recording_sid = form.get("RecordingSid")
    recording_url = form.get("RecordingUrl")
    call_sid = form.get("CallSid")
    recording_duration = form.get("RecordingDuration")

    logger.info(f"[WEBHOOK] /api/recording-status: CallSid={call_sid}, RecordingSid={recording_sid}, RecordingUrl={recording_url}")

    db = get_db()
    if db is not None and call_sid:
        try:
            proxy_url = ""
            if recording_sid:
                proxy_url = f"{Config.get_external_url('api/recording/media')}/{recording_sid}"
            update_doc = {
                "recording_sid": recording_sid,
                "recording_url": proxy_url or recording_url,
                "recording_source_url": recording_url,
                "recording_duration": recording_duration,
                "updated_at": datetime.now(timezone.utc),
            }
            # Update by twilio_call_sid first, fall back to internal call_sid
            await asyncio.to_thread(db.calls.update_one, {"$or": [{"twilio_call_sid": call_sid}, {"call_sid": call_sid}]}, {"$set": update_doc})
            logger.info(f"[WEBHOOK] /api/recording-status: Updated call record for {call_sid}")
        except Exception as e:
            logger.error(f"[WEBHOOK] /api/recording-status: Failed updating DB: {e}")

    return {"success": True}



@app.post("/api/recording/fetch")
async def fetch_recording(request: Request, auth: bool = Depends(verify_api_key)):
    """Force-fetch a recording URL from Twilio using a recording SID and update the calls document.

    Expects JSON body: {"recording_sid": "RE....", "call_sid": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "error": "Invalid JSON"}

    recording_sid = body.get("recording_sid")
    call_sid = body.get("call_sid")
    if not recording_sid:
        return {"success": False, "error": "recording_sid is required"}

    try:
        def _fetch():
            client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            rec = client.recordings(recording_sid).fetch()
            return rec

        rec = await asyncio.to_thread(_fetch)
        recording_url = f"{Config.get_external_url('api/recording/media')}/{recording_sid}"

        # Update DB
        db = get_db()
        if db is not None:
            query = {"recording_sid": recording_sid}
            if call_sid:
                query = {"$or": [{"recording_sid": recording_sid}, {"call_sid": call_sid}, {"twilio_call_sid": call_sid}]}

            await asyncio.to_thread(
                db.calls.update_one,
                query,
                {"$set": {"recording_url": recording_url, "updated_at": datetime.now(timezone.utc)}},
            )

        return {"success": True, "recording_url": recording_url}
    except Exception as e:
        logger.error(f"[API] fetch_recording error: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/recording/media/{recording_sid}")
async def recording_media(recording_sid: str, download: bool = Query(default=False)):
    """Proxy a Twilio recording as direct audio bytes so the browser can play/download without Twilio login."""
    if not Config.TWILIO_ACCOUNT_SID or not Config.TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="Twilio credentials are not configured")

    media_url = f"https://api.twilio.com/2010-04-01/Accounts/{Config.TWILIO_ACCOUNT_SID}/Recordings/{recording_sid}.wav"
    timeout = ClientTimeout(total=30)

    try:
        async with ClientSession(timeout=timeout, auth=BasicAuth(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)) as session:
            async with session.get(media_url) as response:
                if response.status != 200:
                    body = await response.text()
                    raise HTTPException(status_code=response.status, detail=f"Twilio media fetch failed: {body[:200]}")

                content = await response.read()
                headers = {
                    "Content-Type": response.headers.get("Content-Type", "audio/wav"),
                    "Content-Length": str(len(content)),
                }
                if download:
                    headers["Content-Disposition"] = f'attachment; filename="{recording_sid}.wav"'
                return Response(content=content, media_type=headers["Content-Type"], headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] recording_media error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/delete-sentiment/{phone}")
async def delete_sentiment(phone: str, auth: bool = Depends(verify_api_key)):
    """Delete sentiment data for a phone number."""
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    result = await asyncio.to_thread(db.contacts.delete_one, {"phoneNumber": phone})
    
    if result.deleted_count > 0:
        return {"success": True, "message": f"Sentiment data for {phone} deleted successfully"}
    else:
        return {"success": False, "message": f"No contact found for {phone}"}


@app.delete("/api/delete-pending-call/{phone}")
async def delete_pending_call(phone: str, auth: bool = Depends(verify_api_key)):
    """Delete pending call for a phone number."""
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    result = await asyncio.to_thread(db.pending_calls.delete_one, {"phoneNumber": phone})
    
    if result.deleted_count > 0:
        return {"success": True, "message": f"Pending call for {phone} deleted successfully"}
    else:
        return {"success": False, "message": f"No pending call found for {phone}"}


@app.get("/api/assistant-info")
async def assistant_info(auth: bool = Depends(verify_api_key)):
    """Get information about the voice assistant. Matches frontend expectation."""
    return {
        "success": True,
        "assistant": {
            "name": "VOCO",
            "model": Config.GEMINI_MODEL,
            "voice": Config.GEMINI_VOICE,
            "thinking_mode": Config.GEMINI_THINKING,
            "status": "active",
            "active_sessions": gemini_manager.active_count,
        }
    }


@app.get("/api/debug-sessions")
async def debug_sessions(auth: bool = Depends(verify_api_key)):
    """Debug endpoint to see all active Gemini sessions."""
    return {
        "success": True,
        "active_sessions": list(gemini_manager.active_sessions.keys()),
        "active_count": gemini_manager.active_count,
        "active_streams": list(active_streams.keys()),
        "streams_count": len(active_streams),
    }


# ─── CSV Leads Export Endpoint ──────────────────────────────────────────

@app.get("/api/leads/export")
async def export_leads_csv(auth: bool = Depends(verify_api_key)):
    """
    Export leads as CSV file.
    Returns the leads.csv file for download.
    """
    from fastapi.responses import FileResponse
    
    csv_path = get_leads_csv_path()
    
    if not os.path.exists(csv_path):
        # Return empty CSV with headers if file doesn't exist
        from io import StringIO
        import csv
        
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        csv_content = output.getvalue()
        
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=leads.csv"}
        )
    
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename="leads.csv"
    )


@app.post("/api/force-cleanup-sessions")
async def force_cleanup_sessions(auth: bool = Depends(verify_api_key)):
    """Force close all active Gemini sessions. Use this to clean up stale sessions."""
    sessions_to_close = list(gemini_manager.active_sessions.keys())
    closed_count = 0
    
    for call_sid in sessions_to_close:
        try:
            await gemini_manager.close_session(call_sid)
            closed_count += 1
            logger.info(f"[FORCE_CLEANUP] Closed session: {call_sid}")
        except Exception as e:
            logger.error(f"[FORCE_CLEANUP] Error closing session {call_sid}: {e}")
    
    return {
        "success": True,
        "message": f"Closed {closed_count} stale sessions",
        "closed_count": closed_count,
        "remaining_count": gemini_manager.active_count,
    }


# ─── Properties CRUD API Endpoints ────────────────────────────────────

@app.get("/api/properties")
async def get_properties(
    location: str = Query(""),
    property_type: str = Query(""),
    city: str = Query(""),
    auth: bool = Depends(verify_api_key),
):
    """
    Get properties with optional search filters.
    """
    db = get_db()
    
    if db is None:
        return {"success": True, "properties": []}
    
    query = {}
    if location:
        query["location"] = {"$regex": location, "$options": "i"}
    if property_type:
        query["type"] = {"$regex": property_type, "$options": "i"}
    if city:
        query["city"] = {"$regex": city, "$options": "i"}
    
    properties_cursor = await asyncio.to_thread(
        lambda: list(db.properties.find(query).sort("created_at", -1).limit(100))
    )
    properties = properties_cursor
    
    # Convert ObjectId to string for JSON serialization
    for prop in properties:
        prop["_id"] = str(prop["_id"])
    
    return {"success": True, "properties": properties}


@app.get("/api/properties/search")
async def search_properties(
    location: str = Query(""),
    property_type: str = Query(""),
    city: str = Query(""),
    budget_min: str = Query(""),
    budget_max: str = Query(""),
    auth: bool = Depends(verify_api_key),
):
    """
    Advanced property search for Gemini AI tool calls.
    """
    db = get_db()
    
    if db is None:
        return {"success": True, "properties": [], "count": 0}
    
    query = {}
    if location:
        query["location"] = {"$regex": location, "$options": "i"}
    if property_type:
        query["type"] = property_type
    if city:
        query["city"] = {"$regex": city, "$options": "i"}
    
    properties_cursor = await asyncio.to_thread(
        lambda: list(db.properties.find(query).limit(10))
    )
    properties = properties_cursor
    
    # Convert ObjectId to string
    for prop in properties:
        prop["_id"] = str(prop["_id"])
    
    # Format for AI response
    formatted = []
    for prop in properties:
        formatted.append({
            "project_name": prop.get("project_name", "Unknown"),
            "location": prop.get("location", "N/A"),
            "type": prop.get("type", "N/A"),
            "size_sqft": prop.get("size_sqft", "N/A"),
            "price": prop.get("price", "N/A"),
            "amenities": prop.get("amenities", "N/A"),
            "status": prop.get("status", "N/A"),
            "possession_date": prop.get("possession_date", "N/A"),
        })
    
    return {
        "success": True,
        "properties": formatted,
        "count": len(formatted),
    }


@app.post("/api/properties")
async def create_property(
    property_data: PropertyRequest,
    auth: bool = Depends(verify_api_key),
):
    """
    Create a new property.
    """
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    prop_doc = {
        "project_name": property_data.project_name,
        "developer": property_data.developer,
        "location": property_data.location,
        "city": property_data.city,
        "type": property_data.type,
        "size_sqft": property_data.size_sqft,
        "price": property_data.price,
        "price_value": property_data.price_value,
        "amenities": property_data.amenities,
        "status": property_data.status,
        "floors": property_data.floors,
        "possession_date": property_data.possession_date,
        "description": property_data.description,
        "created_at": datetime.now(timezone.utc),
    }
    
    result = await asyncio.to_thread(db.properties.insert_one, prop_doc)
    logger.info("Created property: %s with ID: %s", property_data.project_name, result.inserted_id)
    
    return {
        "success": True,
        "message": f"Property '{property_data.project_name}' created successfully",
        "property_id": str(result.inserted_id),
    }


@app.put("/api/properties/{property_id}")
async def update_property(
    property_id: str,
    property_data: PropertyRequest,
    auth: bool = Depends(verify_api_key),
):
    """
    Update an existing property.
    """
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    update_doc = {
        "project_name": property_data.project_name,
        "developer": property_data.developer,
        "location": property_data.location,
        "city": property_data.city,
        "type": property_data.type,
        "size_sqft": property_data.size_sqft,
        "price": property_data.price,
        "price_value": property_data.price_value,
        "amenities": property_data.amenities,
        "status": property_data.status,
        "floors": property_data.floors,
        "possession_date": property_data.possession_date,
        "description": property_data.description,
        "updated_at": datetime.now(timezone.utc),
    }
    
    result = await asyncio.to_thread(
        db.properties.update_one,
        {"_id": ObjectId(property_id)},
        {"$set": update_doc}
    )
    
    if result.modified_count > 0:
        return {
            "success": True,
            "message": f"Property updated successfully",
        }
    else:
        return {"success": False, "message": "Property not found or no changes made"}


@app.delete("/api/properties/{property_id}")
async def delete_property(
    property_id: str,
    auth: bool = Depends(verify_api_key),
):
    """
    Delete a property.
    """
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    result = await asyncio.to_thread(
        db.properties.delete_one,
        {"_id": ObjectId(property_id)}
    )
    
    if result.deleted_count > 0:
        return {"success": True, "message": "Property deleted successfully"}
    else:
        return {"success": False, "message": "Property not found"}


# ─── Appointments CRUD API Endpoints ──────────────────────────────────

class AppointmentStatusUpdate(BaseModel):
    """Request model for updating appointment status."""
    status: str  # scheduled, confirmed, completed, cancelled


class AppointmentCreateRequest(BaseModel):
    """Request model for creating an appointment from the call screen."""
    customer_name: str
    customer_phone: str
    appointment_type: str = "site_visit"
    property_of_interest: str | None = None
    preferred_date: str
    preferred_time: str
    notes: str | None = None
    call_sid: str | None = None
    whatsapp_number: str | None = None


@app.get("/api/appointments")
async def get_appointments(
    status: str = Query(""),
    auth: bool = Depends(verify_api_key),
):
    """
    Get appointments with optional status filter.
    """
    db = get_db()
    
    if db is None:
        return {"success": True, "appointments": []}
    
    query = {"appointment_type": {"$exists": True}}
    if status:
        query["status"] = status
    
    appointments_cursor = await asyncio.to_thread(
        lambda: list(db.pending_calls.find(query).sort("created_at", -1).limit(100))
    )
    appointments = appointments_cursor
    
    # Convert ObjectId to string for JSON serialization
    for appt in appointments:
        appt["_id"] = str(appt["_id"])
        for field in ["created_at", "preferred_date"]:
            if field in appt and appt[field]:
                if hasattr(appt[field], "isoformat"):
                    appt[field] = appt[field].isoformat()
    
    return {"success": True, "appointments": appointments}


@app.post("/api/appointments")
async def create_appointment(payload: AppointmentCreateRequest, auth: bool = Depends(verify_api_key)):
    """Create a new appointment in pending_calls."""
    db = get_db()

    if db is None:
        return {"success": False, "message": "Database not available"}

    appointment_type = payload.appointment_type or "site_visit"
    if appointment_type not in ["site_visit", "callback"]:
        return {"success": False, "message": "Invalid appointment type"}

    if not payload.customer_name.strip() or not payload.customer_phone.strip():
        return {"success": False, "message": "Customer name and phone are required"}

    if not payload.preferred_date.strip() or not payload.preferred_time.strip():
        return {"success": False, "message": "Preferred date and time are required"}

    is_valid_date, date_msg = validate_preferred_date_future_only(payload.preferred_date.strip())
    if not is_valid_date:
        return {"success": False, "message": date_msg}

    date_check = {"preferred_date": payload.preferred_date, "status": {"$in": ["scheduled", "confirmed"]}}
    existing_count = await asyncio.to_thread(db.pending_calls.count_documents, date_check)
    if existing_count >= Config.MAX_APPOINTMENTS_PER_DAY:
        return {
            "success": False,
            "message": f"Maximum limit of {Config.MAX_APPOINTMENTS_PER_DAY} appointments already reached for {payload.preferred_date}",
        }

    appointment_doc = {
        "customer_name": payload.customer_name.strip(),
        "customer_phone": payload.customer_phone.strip(),
        "appointment_type": appointment_type,
        "property_of_interest": (payload.property_of_interest or "").strip(),
        "preferred_date": payload.preferred_date.strip(),
        "preferred_time": payload.preferred_time.strip(),
        "notes": (payload.notes or "").strip(),
        "status": "scheduled",
        "call_sid": (payload.call_sid or "").strip(),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    result = await asyncio.to_thread(db.pending_calls.insert_one, appointment_doc)

    return {
        "success": True,
        "message": "Appointment booked successfully",
        "appointment_id": str(result.inserted_id),
        "appointment": {**appointment_doc, "_id": str(result.inserted_id)},
    }


@app.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str, auth: bool = Depends(verify_api_key)):
    """
    Get a specific appointment by ID.
    """
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    appointment = await asyncio.to_thread(db.pending_calls.find_one, {"_id": ObjectId(appointment_id)})
    
    if appointment is None:
        return {"success": False, "message": "Appointment not found"}
    
    appointment["_id"] = str(appointment["_id"])
    for field in ["created_at", "preferred_date"]:
        if field in appointment and appointment[field]:
            if hasattr(appointment[field], "isoformat"):
                appointment[field] = appointment[field].isoformat()
    
    return {"success": True, "appointment": appointment}


@app.put("/api/appointments/{appointment_id}")
async def update_appointment(
    appointment_id: str,
    update_data: AppointmentStatusUpdate,
    auth: bool = Depends(verify_api_key),
):
    """
    Update appointment status (confirmed, completed, cancelled).
    """
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    valid_statuses = ["scheduled", "confirmed", "completed", "cancelled"]
    if update_data.status not in valid_statuses:
        return {"success": False, "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"}
    
    result = await asyncio.to_thread(
        db.pending_calls.update_one,
        {"_id": ObjectId(appointment_id)},
        {"$set": {"status": update_data.status, "updated_at": datetime.now(timezone.utc)}}
    )
    
    if result.modified_count > 0:
        return {"success": True, "message": f"Appointment status updated to {update_data.status}"}
    else:
        return {"success": False, "message": "Appointment not found or no changes made"}


@app.delete("/api/appointments/{appointment_id}")
async def delete_appointment(appointment_id: str, auth: bool = Depends(verify_api_key)):
    """
    Delete an appointment.
    """
    db = get_db()
    
    if db is None:
        return {"success": False, "message": "Database not available"}
    
    result = await asyncio.to_thread(
        db.pending_calls.delete_one,
        {"_id": ObjectId(appointment_id)}
    )
    
    if result.deleted_count > 0:
        return {"success": True, "message": "Appointment deleted successfully"}
    else:
        return {"success": False, "message": "Appointment not found"}


# ─── WhatsApp Incoming Message Webhooks ───────────────────────────────

class WhatsAppIncomingMessage(BaseModel):
    """Model for incoming WhatsApp message from Twilio webhook."""
    From: str  # WhatsApp user's phone number
    Body: str  # Message text
    MessageSid: str  # Unique message ID


@app.post("/whatsapp/incoming")
@app.post("/whatsappincoming")
@app.post("/incoming")
async def handle_whatsapp_incoming(request: Request):
    """
    Handle incoming WhatsApp messages from Twilio WhatsApp Sandbox.
    
    Twilio sends a POST request with form data containing:
    - From: Sender's phone number (e.g., +1xxxxxxxxxx)
    - Body: The message text
    - MessageSid: Unique message ID
    
    This endpoint processes the incoming message and can:
    1. Store it in the database
    2. Trigger automated responses
    3. Forward to Gemini for intelligent response
    """
    form_data = await request.form()
    
    from_number = form_data.get("From", "")
    message_body = form_data.get("Body", "")
    message_sid = form_data.get("MessageSid", "")
    
    logger.info(f"[WHATSAPP] Incoming message from {from_number}: {message_body}")
    logger.info(f"[WHATSAPP] Message SID: {message_sid}")
    
    # Store message in database
    db = get_db()
    if db is not None:
        await asyncio.to_thread(
            db.whatsapp_messages.insert_one,
            {
                "from_number": from_number,
                "message_body": message_body,
                "message_sid": message_sid,
                "direction": "inbound",
                "received_at": datetime.now(timezone.utc),
            }
        )
    
    try:
        global whatsapp_bot_manager
        if whatsapp_bot_manager is None:
            whatsapp_bot_manager = WhatsAppConversationManager(get_db, _trigger_outbound_call, _call_lookup_filter)

        await whatsapp_bot_manager.process_message(from_number, message_body, message_sid)
    except Exception as e:
        logger.error(f"[WHATSAPP] Failed to process bot message: {e}")
    
    # Return TwiML response
    return Response(content="<Response></Response>", media_type="application/xml")


@app.post("/whatsapp/status")
async def handle_whatsapp_status(request: Request):
    """
    Handle WhatsApp message status updates from Twilio.
    
    This receives callbacks about message delivery status.
    """
    form_data = await request.form()
    
    message_sid = form_data.get("MessageSid", "")
    message_status = form_data.get("MessageStatus", "")
    
    logger.info(f"[WHATSAPP] Status update - SID: {message_sid}, Status: {message_status}")
    
    # Update message status in database if available
    db = get_db()
    if db is not None:
        await asyncio.to_thread(
            db.whatsapp_messages.update_one,
            {"message_sid": message_sid},
            {"$set": {"status": message_status, "updated_at": datetime.now(timezone.utc)}}
        )
    
    return Response(status_code=200)


# ─── Main Entry Point ─────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host=Config.SERVER_HOST,
        port=Config.SERVER_PORT,
        reload=True,
    )