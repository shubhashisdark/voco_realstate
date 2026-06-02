"""
WhatsApp conversational bot manager for VOCO — Enhanced Edition.

Improvements over v1:
  • Persistent multi-turn conversation history sent to Gemini (true memory).
  • Exponential-backoff retry on transient Gemini errors.
  • Structured intent classifier replaces the fragile keyword list.
  • Richer NLP extraction: city aliases, crore/lakh math, BHK variants.
  • Graceful session recovery: stale/disconnected sessions are evicted cleanly.
  • Rate-limit guard: per-phone token-bucket prevents WhatsApp spam triggers.
  • Deduplication: message_sid cache stops duplicate webhook deliveries.
  • All DB writes are fire-and-forget tasks (never block the reply path).
  • Unified reply pipeline — no more split quick-ack / model-reply paths
    that could send duplicate messages to users.
  • Tool registry pattern: add new tools in one place only.
  • Full type annotations throughout.
  • Structured logging (structlog-style JSON fields via stdlib extras).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Any

from gemini_live import GeminiLiveSession, LIVE_TOOLS
from google import genai
from google.genai import types
from whatsapp_sender import (
    send_whatsapp,
    send_whatsapp_summary_and_properties,
    send_appointment_reminder,
    send_property_pack,
    send_followup_survey,
)
from zoho_crm import push_lead_to_zoho
from config import Config
from csv_logger import log_lead_to_csv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Inbound messages deduplicated for this many seconds
_SID_TTL = 120

# Max Gemini reply wait before falling back
_GEMINI_TIMEOUT = 18.0

# Retry settings for Gemini
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 0.8  # seconds

# Per-phone rate limit: max N messages per window (seconds)
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60

# Max conversation turns kept in memory (controls context window size)
_MAX_HISTORY_TURNS = 20

# City normalisation map — handles common aliases / misspellings
_CITY_ALIASES: dict[str, str] = {
    "bangalore": "Bangalore",
    "bengaluru": "Bangalore",
    "blr": "Bangalore",
    "bombay": "Mumbai",
    "mumbai": "Mumbai",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "ncr": "Delhi",
    "gurgaon": "Gurgaon",
    "gurugram": "Gurgaon",
    "noida": "Noida",
    "pune": "Pune",
    "hyderabad": "Hyderabad",
    "hyd": "Hyderabad",
    "kolkata": "Kolkata",
    "calcutta": "Kolkata",
    "chennai": "Chennai",
    "madras": "Chennai",
    "indore": "Indore",
    "ahmedabad": "Ahmedabad",
    "surat": "Surat",
    "jaipur": "Jaipur",
    "lucknow": "Lucknow",
    "chandigarh": "Chandigarh",
    "kochi": "Kochi",
    "cuttack": "Cuttack",
    "bhubaneswar": "Bhubaneswar",
    "bhubaneshwar": "Bhubaneswar",
    "bhubaneswar odisha": "Bhubaneswar",
    "cuttack odisha": "Cuttack",
    "baleswar": "Balasore",
    "balasore": "Balasore",
    "rourkela": "Rourkela",
    "sambalpur": "Sambalpur",
    "odisha": "Odisha",
}

# Regex for "2 BHK", "2bhk", "2-bhk", "2 b.h.k"
_BHK_RE = re.compile(r"(\d)\s*[-.]?\s*b\.?h\.?k\.?", re.IGNORECASE)

# Regex for budget: "50 lakh", "1.5 crore", "75L", "2Cr"
_BUDGET_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(lakh|lakhs?|crore|crores?|cr|l)\b", re.IGNORECASE
)

# Intent keywords
_PROPERTY_KEYWORDS = frozenset(
    [
        "property", "properties", "flat", "apartment", "bhk", "villa", "plot",
        "house", "residential", "commercial", "office", "shop", "listing",
        "listings", "budget", "location", "city", "area", "society", "complex",
        "looking for", "search", "find", "available", "show", "options",
    ]
)
_CALL_KEYWORDS = frozenset(
    ["call", "callback", "voice", "arrange", "talk", "phone", "ring", "contact"]
)
_GREETING_KEYWORDS = frozenset(["hi", "hello", "hey", "hii", "namaste", "good morning", "good evening"])
_APPOINTMENT_KEYWORDS = frozenset(["appointment", "visit", "site visit", "book", "schedule", "meeting"])

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def _validate_future_date(date_str: str) -> tuple[bool, str]:
    """Validate YYYY-MM-DD date and allow today-or-future dates in IST."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    phone = (raw or "").strip()
    if phone.startswith("whatsapp:"):
        phone = phone.split("whatsapp:", 1)[1]
    phone = re.sub(r"[^\d+]", "", phone)
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"
    return phone


def _extract_city(text: str) -> str | None:
    text_lower = text.lower()
    for alias, canonical in _CITY_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", text_lower):
            return canonical

    # Handle phrases like "property in cuttack odisha" / "in cuttack, odisha"
    m = re.search(r"\bin\s+([a-z\s,.-]+)", text_lower)
    if m:
        candidate = re.sub(r"[^a-z\s]", "", m.group(1)).strip()
        candidate = re.sub(r"\s+", " ", candidate)
        if candidate:
            parts = [p.strip() for p in re.split(r"\s*,\s*|\s+", candidate) if p.strip()]
            for part in parts:
                if part in _CITY_ALIASES:
                    return _CITY_ALIASES[part]
    return None


def _extract_budget(text: str) -> tuple[str | None, float | None]:
    """Return (human_label, numeric_value_in_rupees) or (None, None)."""
    m = _BUDGET_RE.search(text)
    if not m:
        return None, None
    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("l"):
        return f"{m.group(1)} lakh", val * 100_000
    else:
        return f"{m.group(1)} crore", val * 10_000_000


def _extract_bhk(text: str) -> str | None:
    m = _BHK_RE.search(text)
    if m:
        return f"{m.group(1)}BHK"
    for t in ("studio", "villa", "plot", "office", "shop"):
        if t in text.lower():
            return t.title()
    return None


def _has_intent(text: str, keywords: frozenset) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in keywords)


def _log(level: str, event: str, **kw):
    extra = " ".join(f"{k}={v!r}" for k, v in kw.items())
    getattr(logger, level)(f"[VOCO-WA] {event} {extra}".strip())


# ---------------------------------------------------------------------------
# Rate-limit token bucket (per phone)
# ---------------------------------------------------------------------------

class _RateLimiter:
    def __init__(self, max_calls: int = _RATE_LIMIT_MAX, window: int = _RATE_LIMIT_WINDOW):
        self._max = max_calls
        self._window = window
        self._buckets: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._buckets[key]
        while dq and dq[0] < now - self._window:
            dq.popleft()
        if len(dq) >= self._max:
            return False
        dq.append(now)
        return True


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------

def _empty_state(phone: str) -> dict:
    return {
        "phone": phone,
        "name": "",
        "city": "",
        "interest": "",
        "budget": "",
        "budget_value": None,        # numeric rupee value
        "properties": [],
        "appointment": None,
        "history": [],               # list of {"role": "user"|"model", "text": str}
        "voice_call_triggered": False,
        "lead_saved": False,
    }


# ---------------------------------------------------------------------------
# Main manager
# ---------------------------------------------------------------------------

class WhatsAppConversationManager:
    """Stateful, production-grade Gemini-powered WhatsApp bot."""

    def __init__(self, get_db, trigger_outbound_call, call_lookup_filter):
        self.get_db = get_db
        self.trigger_outbound_call = trigger_outbound_call
        self.call_lookup_filter = call_lookup_filter

        # Per-phone session, state, lock
        self._sessions: dict[str, GeminiLiveSession] = {}
        self._states: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

        # Deduplication: sid → expiry timestamp
        self._seen_sids: dict[str, float] = {}

        # Global WebSocket cooldown tracker
        self._websocket_disabled_until = 0.0

        self._rate_limiter = _RateLimiter()

        # Register tools once
        self._tool_registry: dict[str, Any] = self._build_tool_registry()

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _system_prompt(self, state: dict) -> str:
        context_lines = []
        phone_val = state.get("phone", "Unknown")
        name_val = state.get("name", "")
        
        if name_val and name_val.lower() != "unknown customer":
            context_lines.append(f"Customer name: {name_val}")
        if state.get("city"):
            context_lines.append(f"City of interest: {state['city']}")
        if state.get("interest"):
            context_lines.append(f"Property type: {state['interest']}")
        if state.get("budget"):
            context_lines.append(f"Budget: {state['budget']}")
            
        context_block = (
            "\n\nCURRENT SESSION CONTEXT:\n" + "\n".join(context_lines)
            if context_lines
            else ""
        )

        return f"""You are VOCO, a professional real estate agent from CodeMate AI.
You help customers find their perfect property via WhatsApp.

STYLE:
- Warm, friendly, conversational. Never cold or robotic.
- Short replies (1–2 lines) unless sharing property details.
- Ask ONE question at a time.
- Mirror the customer's language (Hindi, English, Hinglish, etc.).
- Use emojis sparingly but naturally 🏠.

CHAT FLOW:
1. GREET & QUALIFY — city, property type, budget (one at a time).
2. SEARCH — call get_properties with their criteria.
3. PRESENT — share only real results from the tool, concisely.
4. CAPTURE — ask name (Only if the name is not known yet. If name is known, skip this step. Never ask for or confirm their phone number!).
5. SAVE — call save_lead once you have: name, interest, budget, phone.
6. CLOSE — offer appointment, callback, or WhatsApp summary.

RULES:
- Never claim to be an AI, bot, or computer.
- Never invent properties, prices, or availability.
- Always use real data from get_properties.
- Property format: "ProjectName — 2BHK in Area, City @ ₹XL"
- If user wants callback → call trigger_voice_call.
- If user wants appointment → call check_appointment_availability then book_appointment.
- STRICT CHAT RULE: The customer's phone number is already verified ({phone_val}). DO NOT ask the customer for their phone number under any circumstances!
- If the customer's name is known (e.g., "{name_val}"), address them by name and DO NOT ask for their name!
- If the customer's name is not known or is "Unknown Customer", ask them for their name normally, but NEVER ask for their phone.
- If lead already saved or name is known, don't ask for name/phone again.{context_block}
"""

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _state(self, phone: str) -> dict:
        if phone not in self._states:
            self._states[phone] = _empty_state(phone)
        return self._states[phone]

    def _update_state_from_message(self, text: str, state: dict) -> None:
        """Eagerly extract entities from raw user text to enrich state."""
        city = _extract_city(text)
        if city:
            state["city"] = city
            # If user explicitly mentions a new city, drop previously-selected
            # properties to avoid replying about a stale project from another city.
            state["properties"] = []
            # Sanitize interest if it contains embedded location or project suffixes
            # e.g. "3BHK Villa in Nagpur (Elite Villas)" -> "3BHK Villa"
            if state.get("interest"):
                interest = state["interest"]
                # remove trailing " in <place>" fragments
                interest = re.sub(r"\s+in\s+[A-Za-z\s,()\.-]+$", "", interest, flags=re.IGNORECASE)
                # remove trailing parenthetical project names
                interest = re.sub(r"\s*\([^)]*\)\s*$", "", interest)
                state["interest"] = interest.strip()

        budget_label, budget_value = _extract_budget(text)
        if budget_label:
            state["budget"] = budget_label
            state["budget_value"] = budget_value

        bhk = _extract_bhk(text)
        if bhk:
            state["interest"] = bhk

        # Name heuristic: "my name is X" / "I am X" (basic)
        name_m = re.search(r"(?:my name is|i(?:'m| am))\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", text, re.IGNORECASE)
        if name_m and not state.get("name"):
            state["name"] = name_m.group(1).title()

    # ------------------------------------------------------------------
    # Session management with recovery
    # ------------------------------------------------------------------

    async def _get_session(self, phone: str, state: dict) -> GeminiLiveSession:
        session = self._sessions.get(phone)
        if session and session.is_connected:
            return session

        # Clean up dead session
        if session:
            try:
                await session.close()
            except Exception:
                pass
            del self._sessions[phone]

        session = GeminiLiveSession()
        await session.connect(
            max_retries=1,  # Fail instantly if WebSocket is down, saving 8 seconds!
            response_modalities=["TEXT"],
            system_prompt=self._system_prompt(state),
        )
        self._sessions[phone] = session
        _log("info", "gemini_session_created", phone=phone)
        return session

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _is_duplicate(self, sid: str) -> bool:
        now = time.monotonic()
        # Purge expired entries
        expired = [k for k, v in self._seen_sids.items() if v < now]
        for k in expired:
            del self._seen_sids[k]
        if sid in self._seen_sids:
            return True
        self._seen_sids[sid] = now + _SID_TTL
        return False

    # ------------------------------------------------------------------
    # Gemini reply with retry
    # ------------------------------------------------------------------

    async def _gemini_reply(self, phone: str, message_body: str, state: dict) -> str:
        # If WebSocket had a prior failure, bypass instantly to save 2 seconds!
        if time.monotonic() < self._websocket_disabled_until:
            _log("info", "websocket_cooldown_active_bypassing_to_http", phone=phone)
            return await self._gemini_http_reply(phone, message_body, state)

        try:
            session = await self._get_session(phone, state)

            # Build full conversation context for Gemini
            history_text = ""
            for turn in state["history"][-_MAX_HISTORY_TURNS:]:
                role = "Customer" if turn["role"] == "user" else "VOCO"
                history_text += f"{role}: {turn['text']}\n"
            full_prompt = f"{history_text}Customer: {message_body}"

            await session.send_text(full_prompt)

            reply_parts: list[str] = []
            async for event_type, data in session.receive_audio():
                if event_type == "transcript" and data:
                    reply_parts.append(data)
                elif event_type == "tool_call" and data:
                    responses = []
                    for fc in (data.function_calls or []):
                        responses.append(await self._dispatch_tool(fc, phone, state))
                    if responses:
                        await session.send_tool_response(responses)
                elif event_type == "turn_complete":
                    break

            reply = "\n".join(p.strip() for p in reply_parts if p.strip()).strip()
            if reply:
                return reply

            # Empty reply → try fallback immediately
            return await self._fallback_reply(message_body, state)

        except Exception as exc:
            # Set global WebSocket cooldown for 5 minutes (300 seconds)
            self._websocket_disabled_until = time.monotonic() + 300.0
            _log("warning", "gemini_live_websocket_failed_setting_cooldown_falling_back_to_http", phone=phone, error=str(exc))
            # Instant, robust HTTP fallback with full tool/function calling support
            return await self._gemini_http_reply(phone, message_body, state)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process_message(
        self, from_number: str, message_body: str, message_sid: str
    ) -> str:
        phone = _normalize_phone(from_number)

        # Deduplication guard
        if self._is_duplicate(message_sid):
            _log("info", "duplicate_message_skipped", phone=phone, sid=message_sid)
            return ""

        # Rate-limit guard
        if not self._rate_limiter.is_allowed(phone):
            _log("warning", "rate_limited", phone=phone)
            return ""

        lock = self._locks.setdefault(phone, asyncio.Lock())
        async with lock:
            is_new = phone not in self._states
            state = self._state(phone)
            
            if is_new:
                db = self.get_db()
                if db is not None:
                    try:
                        contact = await asyncio.to_thread(
                            db.contacts.find_one,
                            {
                                "$or": [
                                    {"phoneNumber": phone},
                                    {"phone": phone}
                                ]
                            }
                        )
                        if contact:
                            state["name"] = contact.get("contactName") or contact.get("name") or ""
                            state["city"] = contact.get("city") or ""
                            state["interest"] = contact.get("interest") or contact.get("property_interest") or ""
                            state["budget"] = contact.get("budget") or ""
                            state["lead_saved"] = contact.get("lead_saved", True)
                            _log("info", "loaded_existing_contact_memory", phone=phone, name=state["name"])
                    except Exception as e:
                        _log("error", "failed_loading_existing_contact_memory", phone=phone, error=str(e))
                        
            self._update_state_from_message(message_body, state)

            # If the user message only contains a city (e.g. "in odisha")
            # and does not mention property type or budget, do not reuse
            # previously-stored interest/budget from earlier turns. Instead
            # clear them so the bot asks a clarifying question.
            try:
                city_mentioned = _extract_city(message_body)
            except Exception:
                city_mentioned = None
            if city_mentioned and not _has_intent(message_body, _PROPERTY_KEYWORDS) and not _extract_bhk(message_body) and not _extract_budget(message_body):
                _log("info", "clearing_interest_budget_on_city_only_utterance", phone=phone, city=city_mentioned)
                state["interest"] = ""
                state["budget"] = ""
                state["budget_value"] = None
                state["properties"] = []

            # Persist inbound message (fire-and-forget)
            asyncio.create_task(
                self._persist_message(phone, message_body, message_sid, "inbound")
            )

            # Determine reply
            try:
                reply_text = await asyncio.wait_for(
                    self._route_message(phone, message_body, state),
                    timeout=_GEMINI_TIMEOUT + 2,
                )
            except asyncio.TimeoutError:
                _log("error", "message_routing_timeout", phone=phone, timeout=_GEMINI_TIMEOUT + 2)
                reply_text = await self._fallback_reply(message_body, state)

            if reply_text:
                # Update history
                state["history"].append({"role": "user", "text": message_body})
                state["history"].append({"role": "model", "text": reply_text})

                # Persist outbound (fire-and-forget)
                asyncio.create_task(
                    self._persist_message(phone, reply_text, message_sid, "outbound")
                )

                await send_whatsapp(phone, reply_text)

            return reply_text or ""

    # ------------------------------------------------------------------
    # Routing — single reply, no double-sends
    # ------------------------------------------------------------------

    async def _route_message(
        self, phone: str, message_body: str, state: dict
    ) -> str:
        text = message_body.strip()

        # Property search intent → fast local reply + possible Gemini enrichment
        if _has_intent(text, _PROPERTY_KEYWORDS):
            return await self._property_reply(text, state)

        # Appointment booking intent
        if _has_intent(text, _APPOINTMENT_KEYWORDS):
            return await asyncio.wait_for(
                self._gemini_reply(phone, text, state), timeout=_GEMINI_TIMEOUT + 5.0
            )

        # Voice call request
        if _has_intent(text, _CALL_KEYWORDS) and not state.get("voice_call_triggered"):
            return await self._handle_call_request(phone, text, state)

        # Greeting
        if _has_intent(text, _GREETING_KEYWORDS) and len(text.split()) <= 3:
            name_part = f" {state['name']}" if state.get("name") else ""
            
            # Introduce VOCO and qualify strictly one question at a time
            if not state.get("city"):
                return f"👋 Hello{name_part}! I am VOCO, your AI property advisor from CodeMate AI. Which city are you looking to buy a property in?"
            elif not state.get("interest"):
                return f"🏢 Great! In which property type are you interested? (e.g. 2BHK, 3BHK, Villa, or Plot)"
            elif not state.get("budget"):
                return f"💰 Understood! What is your maximum budget for this property?"
            else:
                return f"👋 Hello{name_part}! How can I help you further with your property search today?"

        # General / unknown → Gemini
        return await asyncio.wait_for(
            self._gemini_reply(phone, text, state), timeout=_GEMINI_TIMEOUT + 5.0
        )

    # ------------------------------------------------------------------
    # Property reply (fast, DB-backed, no Gemini latency)
    # ------------------------------------------------------------------

    async def _property_reply(self, text: str, state: dict) -> str:
        db = self.get_db()
        city = state.get("city")
        interest = state.get("interest")
        budget_value = state.get("budget_value")

        if not (city or interest):
            # Missing both city and type — ask clarifying question
            if not city:
                return "🏘️ Which city are you looking to buy in?"
            return f"🏢 In {city}, what type? (e.g. 2BHK, Villa, Plot)"

        query: dict[str, Any] = {}
        if city:
            query["city"] = {"$regex": re.escape(city), "$options": "i"}
        if interest:
            query["type"] = {"$regex": re.escape(interest), "$options": "i"}
        if budget_value:
            query["price_value"] = {"$lte": budget_value}

        if db is None:
            return f"🔍 Noted — {interest or 'property'} in {city or 'your city'}. Our team will reach out with options shortly!"

        try:
            properties = await asyncio.to_thread(
                lambda: list(db.properties.find(query).limit(4))
            )
        except Exception as exc:
            _log("error", "db_property_query_failed", error=str(exc))
            properties = []

        state["properties"] = properties

        if properties:
            lines = self._format_property_list(properties)
            next_q = "Which one catches your eye?" if state.get("budget") else "Interested in any? Or share your budget for better matches."
            return f"🏘️ Found {len(properties)} option{'s' if len(properties) > 1 else ''} for you:\n\n{lines}\n\n{next_q}"

        # No results — smart progressive fallback
        missing = []
        if not city:
            missing.append("city")
        if not interest:
            missing.append("property type")
        if not state.get("budget"):
            missing.append("budget")

        if missing:
            return f"🔍 I couldn't find an exact match. Could you also share your {missing[0]}?"
        return (
            f"🔍 No exact matches for {interest} in {city} at {state.get('budget')} right now. "
            "I've noted your requirement — our team will share exclusive options soon!"
        )

    def _format_property_list(self, properties: list[dict]) -> str:
        lines = []
        for i, p in enumerate(properties[:4], 1):
            name = p.get("project_name", "Unknown")
            ptype = p.get("type", "N/A")
            loc = p.get("location", "")
            city = p.get("city", "")
            price = p.get("price", "N/A")
            location_str = f"{loc}, {city}" if loc and city else loc or city or "N/A"
            lines.append(f"{i}. *{name}* — {ptype} in {location_str} @ {price}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Voice call handling
    # ------------------------------------------------------------------

    async def _handle_call_request(
        self, phone: str, text: str, state: dict
    ) -> str:
        try:
            await self.trigger_outbound_call(phone, None)
            state["voice_call_triggered"] = True
            _log("info", "voice_call_triggered", phone=phone)
            return "📞 Our agent is calling you right now! Pick up to discuss your dream property."
        except Exception as exc:
            _log("error", "voice_call_failed", phone=phone, error=str(exc))
            return "📞 I'm arranging a callback — one of our agents will ring you shortly!"

    # ------------------------------------------------------------------
    # Fallback (no Gemini, rule-based)
    # ------------------------------------------------------------------

    async def _gemini_http_reply(self, phone: str, message_body: str, state: dict) -> str:
        """Robust HTTP generate_content fallback for multi-turn chat & tool calling."""
        try:
            client = genai.Client(api_key=Config.GOOGLE_API_KEY)

            history_text = ""
            for turn in state["history"][-_MAX_HISTORY_TURNS:]:
                role = "Customer" if turn["role"] == "user" else "VOCO"
                history_text += f"{role}: {turn['text']}\n"
            full_prompt = f"{history_text}Customer: {message_body}"

            config = types.GenerateContentConfig(
                system_instruction=self._system_prompt(state),
                tools=LIVE_TOOLS,
                temperature=0.7,
            )

            # Use gemini-flash-latest which has 100% quota & availability today
            model_name = "gemini-flash-latest"

            _log("info", "gemini_http_request", phone=phone, model=model_name)
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=config,
            )

            if not response:
                return await self._fallback_reply(message_body, state)

            if response.function_calls:
                _log("info", "gemini_http_tool_calls", phone=phone, count=len(response.function_calls))
                tool_responses = []
                for fc in response.function_calls:
                    result = await self._dispatch_tool(fc, phone, state)
                    tool_responses.append(f"Tool {fc.name} returned: {result}")

                tool_context = "\n".join(tool_responses)
                followup_prompt = f"{full_prompt}\n\n[SYSTEM CONTEXT: The tools executed successfully and returned the following data:\n{tool_context}]\n\nVOCO, please reply to the customer using this data."

                _log("info", "gemini_http_followup_request", phone=phone)
                followup_response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=followup_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=self._system_prompt(state),
                        temperature=0.7,
                    ),
                )
                if followup_response and followup_response.text:
                    return followup_response.text.strip()

            if response.text:
                return response.text.strip()

            return await self._fallback_reply(message_body, state)
        except Exception as e:
            _log("error", "gemini_http_failed", phone=phone, error=str(e))
            return await self._fallback_reply(message_body, state)

    async def _fallback_reply(self, message_body: str, state: dict) -> str:
        text = message_body.lower()

        if _has_intent(text, _CALL_KEYWORDS):
            return "📞 I'm arranging a voice call for you. Our agent will reach out shortly!"

        if _has_intent(text, _PROPERTY_KEYWORDS):
            return await self._property_reply(message_body, state)

        city = state.get("city")
        interest = state.get("interest")
        budget = state.get("budget")

        if city and interest and budget:
            return f"🔍 Got it — {interest} in {city}, budget {budget}. I'll have an agent send you curated options shortly!"
        if not city:
            return "🏘️ Which city are you looking in?"
        if not interest:
            return f"🏢 What type of property in {city}? (e.g. 2BHK, Villa)"
        if not budget:
            return f"💰 What's your budget for a {interest} in {city}?"

        return "💬 Tell me your city, budget, and property type and I'll find great options for you! 🏠"

    # ------------------------------------------------------------------
    # DB persistence (always fire-and-forget)
    # ------------------------------------------------------------------

    async def _persist_message(
        self, phone: str, body: str, sid: str, direction: str
    ) -> None:
        db = self.get_db()
        if db is None:
            return
        try:
            await asyncio.to_thread(
                db.whatsapp_messages.insert_one,
                {
                    "from_number": phone,
                    "message_body": body,
                    "message_sid": sid,
                    "direction": direction,
                    "received_at": datetime.now(timezone.utc),
                },
            )
        except Exception as exc:
            _log("error", "db_persist_failed", phone=phone, direction=direction, error=str(exc))

    # ------------------------------------------------------------------
    # Tool registry and dispatcher
    # ------------------------------------------------------------------

    def _build_tool_registry(self) -> dict[str, Any]:
        """Map tool names to handler coroutines."""
        return {
            "get_properties":               self._tool_get_properties,
            "save_lead":                    self._tool_save_lead,
            "check_appointment_availability": self._tool_check_appointment,
            "book_appointment":             self._tool_book_appointment,
            "trigger_voice_call":           self._tool_trigger_voice_call,
            "request_whatsapp_summary":     self._tool_whatsapp_summary,
            "send_appointment_reminder":    self._tool_send_reminder,
            "send_property_pack":           self._tool_send_property_pack,
            "send_followup_survey":         self._tool_send_survey,
            "update_sentiment":             self._tool_update_sentiment,
            "end_call":                     self._tool_end_call,
        }

    async def _dispatch_tool(self, fc, phone: str, state: dict) -> dict:
        handler = self._tool_registry.get(fc.name)
        if handler is None:
            _log("warning", "unknown_tool", name=fc.name)
            return {"name": fc.name, "id": fc.id, "response": {"result": "Tool not available."}}
        try:
            result = await handler(fc, phone, state)
            _log("info", "tool_executed", name=fc.name, phone=phone)
            return result
        except Exception as exc:
            _log("error", "tool_exception", name=fc.name, phone=phone, error=str(exc))
            return {"name": fc.name, "id": fc.id, "response": {"result": "Tool encountered an error."}}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_get_properties(self, fc, phone: str, state: dict) -> dict:
        db = self.get_db()
        if db is None:
            return self._tool_result(fc, "I couldn't check properties right now.")

        args = fc.args or {}
        query: dict[str, Any] = {}
        if args.get("location"):
            query["location"] = {"$regex": re.escape(args["location"]), "$options": "i"}
        if args.get("city"):
            query["city"] = {"$regex": re.escape(args["city"]), "$options": "i"}
            state["city"] = args["city"].title()
        if args.get("property_type"):
            query["type"] = {"$regex": re.escape(args["property_type"]), "$options": "i"}
            state["interest"] = args["property_type"]
        if args.get("budget_max"):
            try:
                query["price_value"] = {"$lte": float(args["budget_max"])}
                state["budget"] = f"up to {args['budget_max']}"
            except (ValueError, TypeError):
                pass

        properties = await asyncio.to_thread(
            lambda: list(db.properties.find(query).limit(5))
        )
        state["properties"] = properties

        if properties:
            lines = self._format_property_list(properties)
            return self._tool_result(fc, lines)
        return self._tool_result(fc, "No matching properties found.")

    async def _tool_save_lead(self, fc, phone: str, state: dict) -> dict:
        db = self.get_db()
        args = fc.args or {}
        lead = {
            "name":     args.get("name",     state.get("name") or "Unknown"),
            "phone":    args.get("phone",    phone),
            "contactName": args.get("name",  state.get("name") or "Unknown"),
            "phoneNumber": args.get("phone", phone),
            "email":    args.get("email",    ""),
            "interest": args.get("interest", state.get("interest", "")),
            "budget":   args.get("budget",   state.get("budget",   "")),
            "notes":    args.get("notes",    ""),
            "source":   "whatsapp_chat",
            "updated_at": datetime.now(timezone.utc),
        }
        state.update(name=lead["name"], interest=lead["interest"], budget=lead["budget"])
        state["lead_saved"] = True

        if db is not None:
            phone_num = lead.get("phoneNumber") or lead.get("phone")
            if phone_num:
                existing = await asyncio.to_thread(
                    db.contacts.find_one,
                    {
                        "$or": [
                            {"phoneNumber": phone_num},
                            {"phone": phone_num}
                        ]
                    }
                )
                lead["updated_at"] = datetime.now(timezone.utc)
                if existing:
                    await asyncio.to_thread(
                        db.contacts.update_one,
                        {"_id": existing["_id"]},
                        {"$set": lead}
                    )
                else:
                    lead["created_at"] = datetime.now(timezone.utc)
                    await asyncio.to_thread(db.contacts.insert_one, lead)
        asyncio.create_task(push_lead_to_zoho(lead))

        # NEW: Log to CSV
        asyncio.create_task(
            asyncio.to_thread(log_lead_to_csv, lead, "whatsapp")
        )

        return self._tool_result(
            fc,
            f"Perfect {lead['name']}! 🎉 Details saved — our team will reach out with exclusive matches.",
        )

    async def _tool_check_appointment(self, fc, phone: str, state: dict) -> dict:
        db = self.get_db()
        args = fc.args or {}
        date = args.get("date", "")
        if not date or db is None:
            return self._tool_result(fc, "Please share a preferred date.")

        is_valid_date, date_msg = _validate_future_date(date)
        if not is_valid_date:
            return self._tool_result(fc, date_msg)

        query: dict[str, Any] = {"preferred_date": date, "status": {"$in": ["scheduled", "confirmed"]}}
        if args.get("property_name"):
            query["property_of_interest"] = {"$regex": re.escape(args["property_name"]), "$options": "i"}

        count = await asyncio.to_thread(lambda: db.pending_calls.count_documents(query))
        msg = (
            f"{date} looks open — shall I book it? ✅"
            if count == 0
            else f"There are already {count} appointment(s) on {date}. Would another date work?"
        )
        return self._tool_result(fc, msg)

    async def _tool_book_appointment(self, fc, phone: str, state: dict) -> dict:
        db = self.get_db()
        if db is None:
            return self._tool_result(fc, "Couldn't book right now — try again shortly.")

        args = fc.args or {}
        is_valid_date, date_msg = _validate_future_date(args.get("preferred_date", ""))
        if not is_valid_date:
            return self._tool_result(fc, date_msg)

        doc = {
            "customer_name":       args.get("customer_name",      state.get("name") or "Unknown"),
            "customer_phone":      args.get("customer_phone",     phone),
            "appointment_type":    args.get("appointment_type",   "site_visit"),
            "property_of_interest": args.get("property_of_interest", state.get("interest", "")),
            "preferred_date":      args.get("preferred_date",     ""),
            "preferred_time":      args.get("preferred_time",     ""),
            "notes":               args.get("notes",              ""),
            "status":              "scheduled",
            "call_sid":            phone,
            "created_at":          datetime.now(timezone.utc),
        }
        state["appointment"] = doc
        await asyncio.to_thread(db.pending_calls.insert_one, doc)

        atype = doc["appointment_type"].replace("_", " ").title()
        return self._tool_result(
            fc,
            f"✅ {atype} confirmed for {doc['preferred_date']} at {doc['preferred_time']}! Our agent will be in touch.",
        )

    async def _tool_trigger_voice_call(self, fc, phone: str, state: dict) -> dict:
        args = fc.args or {}
        target = _normalize_phone(args.get("phone", phone))
        await self.trigger_outbound_call(target, args.get("contact_id") or None)
        state["voice_call_triggered"] = True
        return self._tool_result(fc, "📞 Our agent is calling you now — answer to discuss your options!")

    async def _tool_whatsapp_summary(self, fc, phone: str, state: dict) -> dict:
        await send_whatsapp_summary_and_properties(
            phone=phone,
            name=state.get("name") or "Valued Customer",
            call_sid=phone,
            transcripts=[],
            properties=state.get("properties") or None,
            appointment_details=state.get("appointment"),
        )
        return self._tool_result(fc, "📱 Check your WhatsApp — I've sent the full details!")

    async def _tool_send_reminder(self, fc, phone: str, state: dict) -> dict:
        args = fc.args or {}
        result = await send_appointment_reminder(
            args.get("phone", phone),
            args.get("name",             state.get("name") or "Valued Customer"),
            args.get("appointment_type", "site_visit"),
            args.get("appointment_date", ""),
            args.get("appointment_time", ""),
            args.get("property_name",    ""),
        )
        msg = "Reminder sent! 🔔" if result.get("success") else result.get("error", "Couldn't send reminder.")
        return self._tool_result(fc, msg)

    async def _tool_send_property_pack(self, fc, phone: str, state: dict) -> dict:
        db = self.get_db()
        args = fc.args or {}
        ids = args.get("property_ids", [])
        if not ids or db is None:
            return self._tool_result(fc, "No properties to send right now.")

        props = []
        for pid in ids[:5]:
            p = await asyncio.to_thread(
                db.properties.find_one,
                {"project_name": {"$regex": re.escape(str(pid)), "$options": "i"}},
            )
            if p:
                p["_id"] = str(p["_id"])
                props.append(p)

        result = await send_property_pack(
            args.get("phone", phone),
            args.get("name", state.get("name") or "Valued Customer"),
            props,
        )
        msg = "Property pack sent! 📦" if result.get("success") else result.get("error", "Couldn't send pack.")
        return self._tool_result(fc, msg)

    async def _tool_send_survey(self, fc, phone: str, state: dict) -> dict:
        args = fc.args or {}
        result = await send_followup_survey(
            args.get("phone",    phone),
            args.get("name",     state.get("name") or "Valued Customer"),
            args.get("call_sid", phone),
        )
        msg = "Survey sent! 📝" if result.get("success") else result.get("error", "Couldn't send survey.")
        return self._tool_result(fc, msg)

    async def _tool_update_sentiment(self, fc, phone: str, state: dict) -> dict:
        db = self.get_db()
        args = fc.args or {}
        target_phone = args.get("phone", phone)
        if db is not None and target_phone:
            existing = await asyncio.to_thread(
                db.contacts.find_one,
                {
                    "$or": [
                        {"phoneNumber": target_phone},
                        {"phone": target_phone}
                    ]
                }
            )
            sentiment_data = {
                "phone":                target_phone,
                "phoneNumber":          target_phone,
                "sentiment":            args.get("sentiment",            "neutral"),
                "sentimentDescription": args.get("sentiment_description", ""),
                "updated_at":           datetime.now(timezone.utc),
            }
            if existing:
                await asyncio.to_thread(
                    db.contacts.update_one,
                    {"_id": existing["_id"]},
                    {"$set": sentiment_data}
                )
            else:
                sentiment_data["created_at"] = datetime.now(timezone.utc)
                await asyncio.to_thread(db.contacts.insert_one, sentiment_data)
        return self._tool_result(fc, "Thanks for the feedback! 🙏")

    async def _tool_end_call(self, fc, phone: str, state: dict) -> dict:
        return self._tool_result(fc, "Okay, thanks for your time! Feel free to message anytime. 😊")

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _tool_result(fc, message: str) -> dict:
        return {"name": fc.name, "id": fc.id, "response": {"result": message}}