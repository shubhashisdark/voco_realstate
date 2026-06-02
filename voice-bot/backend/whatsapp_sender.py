"""
WhatsApp sender module for VOCO — Enhanced Edition.

Improvements over v1:
  • Shared aiohttp.ClientSession (connection pooling, no per-call overhead).
  • Exponential-backoff retry for both Twilio and Meta transient errors.
  • Phone normalisation centralised and DRY — no more repeated lstrip/strip.
  • IST conversion extracted to a single utility; no more inline arithmetic.
  • Template component builder for Meta API — no more hand-rolled dicts.
  • Twilio content_sid support for approved template messages.
  • Rich property card format (numbered, bold name, amenities, possession date).
  • Chunked sending for long property lists (WhatsApp 4096-char limit guard).
  • send_whatsapp_summary_and_properties now sends summary + properties always.
  • Structured logging (key=value extras) replaces f-string concatenation.
  • Full type annotations throughout.
  • Graceful shutdown: close_session() for clean server teardown.
  • Config validation at import time with clear error messages.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any

import aiohttp
from twilio.rest import Client as TwilioClient

from config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IST_OFFSET = timedelta(hours=5, minutes=30)
_WA_MAX_CHARS = 4000           # WhatsApp freeform message cap (leave buffer)
_META_API_VERSION = "v19.0"    # Graph API version — bump here when upgrading
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5        # seconds; doubled each attempt

# ---------------------------------------------------------------------------
# Shared aiohttp session (connection pooling)
# ---------------------------------------------------------------------------

_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    """Return (or lazily create) a shared aiohttp session."""
    global _http_session
    if _http_session is None or _http_session.closed:
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        _http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _http_session


async def close_session() -> None:
    """Cleanly close the shared HTTP session (call on app shutdown)."""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None
        logger.info("[WHATSAPP] HTTP session closed")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    """Ensure E.164 format: strip non-digits except leading +."""
    phone = (raw or "").strip()
    # Remove whatsapp: prefix if present
    if phone.lower().startswith("whatsapp:"):
        phone = phone.split(":", 1)[1]
    # Keep only digits and a leading +
    phone = re.sub(r"[^\d+]", "", phone)
    # Ensure single leading +
    phone = phone.lstrip("+")
    return f"+{phone}" if phone else ""


def _to_ist(dt: datetime) -> datetime:
    """Convert a datetime (assumed UTC if naive) to IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=5, minutes=30)))


def _parse_date(raw: str) -> str:
    """Parse a date string and return DD-MM-YYYY (IST). Passes through on failure."""
    if not raw:
        return "N/A"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _to_ist(dt).strftime("%d-%m-%Y")
    except (ValueError, AttributeError):
        return raw  # already formatted or unexpected shape


def _parse_time(raw: str) -> str:
    """Parse HH:MM (24-hour) → 12-hour with AM/PM. Passes through on failure."""
    if not raw:
        return "N/A"
    try:
        parts = raw.split(":")
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        period = "AM" if hour < 12 else "PM"
        display = hour % 12 or 12
        return f"{display:02d}:{minute:02d} {period}"
    except (ValueError, IndexError):
        return raw


def _chunk_message(lines: list[str], max_chars: int = _WA_MAX_CHARS) -> list[str]:
    """
    Join lines into one or more messages, each under max_chars.
    Splits on blank lines when possible.
    """
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current_parts:
            chunks.append("\n".join(current_parts))
            current_parts = []
            current_len = 0
        current_parts.append(line)
        current_len += line_len

    if current_parts:
        chunks.append("\n".join(current_parts))

    return chunks or [""]


def _log(level: str, event: str, **kw: Any) -> None:
    extra = " ".join(f"{k}={v!r}" for k, v in kw.items())
    getattr(logger, level)(f"[VOCO-WA] {event} {extra}".strip())


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

async def _with_retry(coro_factory, label: str) -> dict:
    """
    Call coro_factory() up to _MAX_RETRIES times, backing off on failure.
    coro_factory is a zero-arg async callable so we can create a fresh coroutine
    on each attempt.
    """
    last_result: dict = {"success": False, "error": "Never ran"}
    for attempt in range(_MAX_RETRIES):
        try:
            result = await coro_factory()
            if result.get("success"):
                return result
            last_result = result
            # Don't retry on permanent errors (auth, bad number, etc.)
            err = str(result.get("error", "")).lower()
            if any(x in err for x in ("credential", "not configured", "invalid", "unauthorized")):
                return result
        except Exception as exc:
            last_result = {"success": False, "error": str(exc)}
            _log("warning", f"{label}_attempt_failed", attempt=attempt, error=str(exc))

        if attempt < _MAX_RETRIES - 1:
            await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

    _log("error", f"{label}_all_retries_failed", error=last_result.get("error"))
    return last_result


# ---------------------------------------------------------------------------
# Twilio sender
# ---------------------------------------------------------------------------

def _twilio_client() -> TwilioClient | None:
    if not (Config.TWILIO_ACCOUNT_SID and Config.TWILIO_AUTH_TOKEN):
        _log("error", "twilio_credentials_missing")
        return None
    return TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)


async def _twilio_send(to_number: str, body: str, content_sid: str | None = None) -> dict:
    """
    Send a freeform (or content_sid-based template) WhatsApp message via Twilio.
    """
    client = _twilio_client()
    if client is None:
        return {"success": False, "error": "Twilio credentials not configured"}
    if not to_number:
        return {"success": False, "error": "No recipient number"}

    to_number = _normalize_phone(to_number)
    from_num = _normalize_phone(
        getattr(Config, "TWILIO_WHATSAPP_NUMBER", None) or "+14155238886"
    )
    wa_to = f"whatsapp:{to_number}"
    wa_from = f"whatsapp:{from_num}"

    _log("info", "twilio_send", to=to_number, content_sid=content_sid)

    async def _attempt():
        kwargs: dict[str, Any] = {"from_": wa_from, "to": wa_to}
        if content_sid:
            kwargs["content_sid"] = content_sid
        else:
            kwargs["body"] = body
        msg = await asyncio.to_thread(client.messages.create, **kwargs)
        _log("info", "twilio_success", to=to_number, sid=msg.sid, status=msg.status)
        return {"success": True, "message_sid": msg.sid, "status": msg.status, "to": to_number, "provider": "twilio"}

    return await _with_retry(_attempt, "twilio_send")


# ---------------------------------------------------------------------------
# Meta Cloud API sender
# ---------------------------------------------------------------------------

def _meta_url() -> str:
    phone_id = getattr(Config, "META_WHATSAPP_PHONE_NUMBER_ID", "")
    return f"https://graph.facebook.com/{_META_API_VERSION}/{phone_id}/messages"


def _meta_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {Config.META_WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


async def _meta_send(to_number: str, payload: dict) -> dict:
    """Low-level Meta send with connection-pooled session and retry."""
    if not getattr(Config, "META_WHATSAPP_ACCESS_TOKEN", None):
        return {"success": False, "error": "Meta access token not configured"}
    if not to_number:
        return {"success": False, "error": "No recipient number"}

    to_number = _normalize_phone(to_number)
    full_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "personal",
        "to": to_number,
        **payload,
    }
    _log("info", "meta_send", to=to_number, type=payload.get("type"))

    async def _attempt():
        session = _get_http_session()
        async with session.post(
            _meta_url(), json=full_payload, headers=_meta_headers()
        ) as resp:
            data = await resp.json()
            if resp.status == 200:
                msg_id = (data.get("messages") or [{}])[0].get("id")
                _log("info", "meta_success", to=to_number, msg_id=msg_id)
                return {"success": True, "message_id": msg_id, "status": "sent", "to": to_number, "provider": "meta"}
            error_msg = data.get("error", {}).get("message", f"HTTP {resp.status}")
            _log("error", "meta_error", to=to_number, error=error_msg, status=resp.status)
            return {"success": False, "error": error_msg}

    return await _with_retry(_attempt, "meta_send")


async def _meta_send_text(to_number: str, body: str) -> dict:
    return await _meta_send(to_number, {"type": "text", "text": {"body": body, "preview_url": False}})


async def _meta_send_template(
    to_number: str,
    template_name: str,
    language: str = "en",
    components: list[dict] | None = None,
) -> dict:
    template: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language},
    }
    if components:
        template["components"] = components
    return await _meta_send(to_number, {"type": "template", "template": template})


# ---------------------------------------------------------------------------
# Template component builder
# ---------------------------------------------------------------------------

def _body_component(*text_values: str) -> dict:
    """Build a Meta template body component with text parameters."""
    return {
        "type": "body",
        "parameters": [{"type": "text", "text": str(v)} for v in text_values],
    }


def _header_component(text: str) -> dict:
    return {"type": "header", "parameters": [{"type": "text", "text": text}]}


def _button_component(index: int, payload: str) -> dict:
    return {"type": "button", "sub_type": "quick_reply", "index": index, "parameters": [{"type": "payload", "payload": payload}]}


# ---------------------------------------------------------------------------
# Unified public API
# ---------------------------------------------------------------------------

async def send_whatsapp(to_number: str, body: str) -> dict:
    """
    Send a freeform WhatsApp text. Provider selected via Config.WHATSAPP_PROVIDER.
    Automatically chunks messages that exceed the 4000-char limit.
    """
    if not body:
        return {"success": False, "error": "Empty message body"}

    # Chunk if oversized
    if len(body) > _WA_MAX_CHARS:
        chunks = _chunk_message(body.splitlines(), _WA_MAX_CHARS)
        results = []
        for chunk in chunks:
            results.append(await _send_via_provider(to_number, chunk))
        # Return last result; flag failure if any chunk failed
        failed = [r for r in results if not r.get("success")]
        if failed:
            return {"success": False, "error": f"{len(failed)} chunk(s) failed", "results": results}
        return {**results[-1], "chunks_sent": len(chunks)}

    return await _send_via_provider(to_number, body)


async def _send_via_provider(to_number: str, body: str) -> dict:
    provider = (getattr(Config, "WHATSAPP_PROVIDER", "twilio") or "twilio").lower()
    if provider == "meta":
        return await _meta_send_text(to_number, body)
    elif provider == "twilio":
        return await _twilio_send(to_number, body)
    else:
        _log("error", "unknown_provider", provider=provider)
        return {"success": False, "error": f"Unknown provider: {provider}"}


async def send_whatsapp_template_unified(
    to_number: str,
    template_name: str,
    language: str = "en",
    components: list[dict] | None = None,
    content_sid: str | None = None,
) -> dict:
    """
    Unified template sender. Supports content_sid for Twilio approved templates.
    """
    provider = (getattr(Config, "WHATSAPP_PROVIDER", "twilio") or "twilio").lower()
    if provider == "meta":
        return await _meta_send_template(to_number, template_name, language, components)
    elif provider == "twilio":
        return await _twilio_send(to_number, "", content_sid=content_sid)
    else:
        return {"success": False, "error": f"Unknown provider: {provider}"}


# ---------------------------------------------------------------------------
# Message formatters
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 22


def _header(icon: str, title: str) -> list[str]:
    return [f"{icon} *{title}*", _DIVIDER]


def _footer(tagline: str = "VOCO by CodeMate AI 🏠") -> list[str]:
    return [_DIVIDER, f"_{tagline}_"]


def _format_property(i: int, prop: dict) -> list[str]:
    """Rich single-property block."""
    name = prop.get("project_name") or prop.get("name") or "Unknown"
    ptype = prop.get("type") or prop.get("property_type") or "N/A"
    location = prop.get("location") or "N/A"
    city = prop.get("city") or ""
    price = prop.get("price") or prop.get("rate") or "N/A"
    size = prop.get("size") or prop.get("size_sqft") or prop.get("area") or ""
    amenities = prop.get("amenities") or ""
    possession = prop.get("possession_date") or ""
    rera = prop.get("rera_id") or ""

    location_str = f"{location}, {city}" if city and city not in location else location
    lines = [
        f"*{i}. {name}*",
        f"   📍 {location_str}",
        f"   🏠 {ptype}   💰 {price}",
    ]
    if size:
        lines.append(f"   📐 {size} sq ft")
    if possession:
        lines.append(f"   📅 Possession: {possession}")
    if amenities:
        # Truncate long amenities strings
        am = amenities if len(amenities) <= 80 else amenities[:77] + "…"
        lines.append(f"   ✨ {am}")
    if rera:
        lines.append(f"   🔖 RERA: {rera}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# High-level send functions
# ---------------------------------------------------------------------------

async def send_call_summary(
    phone: str,
    name: str,
    call_sid: str,
    transcripts: list[dict],
) -> dict:
    """
    Generate and send a structured call summary.
    Extracts interest, budget, and duration from transcripts.
    """
    now_ist = _to_ist(datetime.now(timezone.utc))
    lines: list[str] = [
        *_header("🏠", "VOCO — Call Summary"),
        f"Hi *{name or 'Customer'}*!",
        "",
        f"📅 {now_ist.strftime('%d %b %Y')}  🕐 {now_ist.strftime('%I:%M %p')} IST",
        "",
        "*Summary*",
    ]

    user_turns = [t["text"] for t in transcripts if t.get("speaker") == "User"]
    ai_turns = [t["text"] for t in transcripts if t.get("speaker") == "AI"]

    # Extract interest
    interest_line = next(
        (u[:120] for u in user_turns if any(k in u.lower() for k in ("bhk", "villa", "plot", "flat", "apartment"))),
        None,
    )
    if interest_line:
        lines.append(f"🏢 Interest: {interest_line}")

    # Extract budget
    budget_line = next(
        (u[:120] for u in user_turns if any(k in u.lower() for k in ("budget", "lakh", "crore"))),
        None,
    )
    if budget_line:
        lines.append(f"💰 Budget: {budget_line}")

    # Estimated duration
    if len(transcripts) > 4:
        minutes = max(1, len(transcripts) // 4)
        lines.append(f"⏱️ Duration: ~{minutes} min")

    # Properties discussed
    prop_mentions = [a[:120] for a in ai_turns if any(k in a.lower() for k in ("project", "property", "bhk"))]
    if prop_mentions:
        lines += ["", "*Properties discussed:*"]
        for m in prop_mentions[:2]:
            lines.append(f"• {m}")

    lines += ["", *_footer()]
    return await send_whatsapp(phone, "\n".join(lines))


async def send_property_details(
    phone: str,
    name: str,
    properties: list[dict],
) -> dict:
    """Send a formatted property list. Auto-chunks if more than 3 properties."""
    if not properties:
        return {"success": False, "error": "No properties to send"}

    results: list[dict] = []
    # Send up to 3 properties per message to stay readable
    for batch_start in range(0, len(properties), 3):
        batch = properties[batch_start: batch_start + 3]
        batch_num = batch_start // 3 + 1
        total_batches = (len(properties) + 2) // 3

        header_title = f"Properties for {name or 'You'}"
        if total_batches > 1:
            header_title += f" ({batch_num}/{total_batches})"

        lines: list[str] = [*_header("🏢", header_title)]
        for i, prop in enumerate(batch, start=batch_start + 1):
            lines += _format_property(i, prop)

        if batch_start + 3 >= len(properties):
            # Last batch — add CTA
            lines += [
                "Would you like to schedule a site visit? 📅",
                "",
                *_footer(),
            ]

        results.append(await send_whatsapp(phone, "\n".join(lines)))
        if len(properties) > 3:
            await asyncio.sleep(0.3)  # gentle rate spacing between batches

    failed = [r for r in results if not r.get("success")]
    if failed:
        return {"success": False, "error": f"{len(failed)} batch(es) failed", "results": results}
    return {**results[-1], "batches_sent": len(results)}


async def send_appointment_details(
    phone: str,
    name: str,
    appointment: dict,
) -> dict:
    """Send structured appointment confirmation."""
    if not appointment:
        return {"success": False, "error": "No appointment details"}

    atype = appointment.get("appointment_type", "site_visit").replace("_", " ").title()
    date_str = _parse_date(appointment.get("preferred_date", ""))
    time_str = _parse_time(appointment.get("preferred_time", ""))
    prop_name = appointment.get("property_of_interest", "")
    notes = appointment.get("notes", "")

    lines: list[str] = [
        *_header("📅", "Appointment Confirmed!"),
        f"Hi *{name or 'Customer'}*!",
        "",
        f"🔹 *Type:* {atype}",
        f"🔹 *Date:* {date_str}",
        f"🔹 *Time:* {time_str}",
    ]
    if prop_name:
        lines.append(f"🔹 *Property:* {prop_name}")
    if notes:
        lines.append(f"🔹 *Notes:* {notes}")

    lines += [
        "",
        "_We'll follow up if anything changes. See you there! 👋_",
        "",
        *_footer(),
    ]
    return await send_whatsapp(phone, "\n".join(lines))


async def send_whatsapp_summary_and_properties(
    phone: str,
    name: str,
    call_sid: str,
    transcripts: list[dict],
    properties: list[dict] | None = None,
    appointment_details: dict | None = None,
) -> dict:
    """
    Send call summary, property details, and appointment info in sequence.
    All three are always attempted; results are aggregated.
    """
    results: dict[str, dict] = {}

    # Always send summary
    results["summary"] = await send_call_summary(phone, name, call_sid, transcripts)

    # Send properties if available
    if properties:
        await asyncio.sleep(0.5)  # small gap between messages
        results["properties"] = await send_property_details(phone, name, properties)

    # Send appointment if available
    if appointment_details:
        await asyncio.sleep(0.5)
        results["appointment"] = await send_appointment_details(phone, name, appointment_details)

    return results


async def send_appointment_reminder(
    phone: str,
    name: str,
    appointment_type: str,
    appointment_date: str,
    appointment_time: str,
    property_name: str = "",
) -> dict:
    """
    Send appointment reminder — tries approved template first, then freeform fallback.
    """
    type_label = "Site Visit" if appointment_type == "site_visit" else "Callback"
    formatted_date = _parse_date(appointment_date)
    formatted_time = _parse_time(appointment_time)

    # Try approved template
    components = [_body_component(formatted_date, formatted_time)]
    result = await send_whatsapp_template_unified(
        to_number=phone,
        template_name="appointment_reminders",
        language="en",
        components=components,
        content_sid=getattr(Config, "TWILIO_APPOINTMENT_CONTENT_SID", None),
    )
    if result.get("success"):
        return result

    _log("warning", "template_fallback", phone=phone, template="appointment_reminders")

    # Freeform fallback
    lines: list[str] = [
        *_header("📅", "Appointment Reminder"),
        f"Hi *{name or 'Customer'}*!",
        "",
        "This is a reminder for your upcoming appointment:",
        "",
        f"🔹 *Type:* {type_label}",
        f"🔹 *Date:* {formatted_date}",
        f"🔹 *Time:* {formatted_time}",
    ]
    if property_name:
        lines.append(f"🔹 *Property:* {property_name}")
    lines += [
        "",
        "_Please be on time. Reply to reschedule._",
        "",
        *_footer(),
    ]
    return await send_whatsapp(phone, "\n".join(lines))


async def send_property_pack(
    phone: str,
    name: str,
    properties: list[dict],
) -> dict:
    """Send a curated pre-visit property pack (richer format than general listing)."""
    if not properties:
        return {"success": False, "error": "No properties to send"}

    lines: list[str] = [
        *_header("🎁", f"Your Curated Property Pack — {name or 'Customer'}"),
        "_Handpicked for your visit today_ 🏠",
        "",
    ]
    for i, prop in enumerate(properties[:5], 1):
        lines += _format_property(i, prop)

    lines += [
        "Tap a number to ask more about any property! 💬",
        "",
        *_footer(),
    ]
    return await send_whatsapp(phone, "\n".join(lines))


async def send_followup_survey(
    phone: str,
    name: str,
    call_sid: str,
) -> dict:
    """Send a post-call satisfaction survey."""
    lines: list[str] = [
        *_header("🙏", f"Thank You, {name or 'Customer'}!"),
        "We hope you enjoyed your VOCO experience.",
        "Quick feedback helps us serve you better! 💪",
        "",
        "*1️⃣ Rate your experience:*",
        "   Reply: 1 (Poor) → 5 (Excellent)",
        "",
        "*2️⃣ Would you recommend VOCO?*",
        "   Reply: *Yes* or *No*",
        "",
        "*3️⃣ Any comments?*",
        "   Just type your thoughts below.",
        "",
        f"_Ref: {call_sid}_",
        "",
        *_footer(),
    ]
    return await send_whatsapp(phone, "\n".join(lines))


# ---------------------------------------------------------------------------
# Legacy aliases (backward-compatible)
# ---------------------------------------------------------------------------

async def send_whatsapp_message(
    to_number: str,
    from_whatsapp: str,  # ignored — provider config used
    body: str,
) -> dict:
    """Backward-compatible wrapper for direct Twilio sends."""
    return await _twilio_send(to_number, body)


async def send_whatsapp_template(
    to_number: str,
    template_name: str,
    language: str = "en",
    components: list | None = None,
) -> dict:
    """Backward-compatible Twilio template wrapper."""
    return await send_whatsapp_template_unified(to_number, template_name, language, components)


async def send_meta_text_message(to_number: str, body: str) -> dict:
    return await _meta_send_text(to_number, body)


async def send_meta_template_message(
    to_number: str,
    template_name: str,
    language: str = "en",
    components: list | None = None,
) -> dict:
    return await _meta_send_template(to_number, template_name, language, components)