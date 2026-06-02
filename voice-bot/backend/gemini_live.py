"""
Wrapper around the Google Gemini Live API for real-time audio conversation.
"""

import asyncio
import re
import logging
from google import genai
from google.genai import types
from config import Config

logger = logging.getLogger(__name__)


# ─── Tool Declarations for Function Calling ─────────────────────────

SAVE_LEAD_DECLARATION = types.FunctionDeclaration(
    name="save_lead",
    description="Save a customer's contact information and property interest as a lead. Call this whenever the customer provides their name, phone number, or property requirements.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "name": types.Schema(type="STRING", description="Customer's full name"),
            "phone": types.Schema(type="STRING", description="Customer's phone number"),
            "email": types.Schema(type="STRING", description="Customer's email address (if provided)"),
            "interest": types.Schema(type="STRING", description="What type of property the customer is looking for (e.g., '2BHK in Pune')"),
            "budget": types.Schema(type="STRING", description="Customer's budget range (e.g., '50-80 Lakhs')"),
            "notes": types.Schema(type="STRING", description="Any additional notes about the customer's requirements"),
        },
        required=["name"],
    ),
)

END_CALL_DECLARATION = types.FunctionDeclaration(
    name="end_call",
    description="Hang up the phone call. Call this ONLY after you have finished your conversation and said a final goodbye to the customer. Do not call this while the customer is still talking.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "reason": types.Schema(type="STRING", description="Reason for ending the call (e.g., 'conversation_complete', 'customer_said_goodbye')"),
        },
        required=["reason"],
    ),
)

REQUEST_WA_SUMMARY_DECLARATION = types.FunctionDeclaration(
    name="request_whatsapp_summary",
    description="Send the call summary and property details to the customer's WhatsApp number. Call this ONLY after the customer has explicitly given permission to send WhatsApp messages. Ask for their WhatsApp number if not already provided.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "whatsapp_number": types.Schema(type="STRING", description="Customer's WhatsApp number in international format (e.g., +919876543210)"),
            "customer_name": types.Schema(type="STRING", description="The customer's name, so that we can address them by name in the summary."),
            "permission_granted": types.Schema(type="BOOLEAN", description="Whether the customer explicitly agreed to receive WhatsApp messages"),
            "send_properties": types.Schema(type="BOOLEAN", description="Whether to include property details along with the summary"),
        },
        required=["whatsapp_number", "customer_name", "permission_granted"],
    ),
)

GET_PROPERTIES_DECLARATION = types.FunctionDeclaration(
    name="get_properties",
    description="Search for available properties in MongoDB based on customer requirements. Call this whenever the customer asks about property options, locations, prices, or wants to see specific listings. Use the customer's preferences to filter results.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "location": types.Schema(type="STRING", description="Specific location or area the customer is interested in (e.g., 'Whitefield', 'Andheri')"),
            "city": types.Schema(type="STRING", description="City name (e.g., 'Pune', 'Bangalore')"),
            "property_type": types.Schema(type="STRING", description="Property type preference (e.g., '2BHK', '3BHK', 'Villa', 'Plot')"),
            "budget_min": types.Schema(type="STRING", description="Minimum budget mentioned by customer (e.g., '50 Lakhs')"),
            "budget_max": types.Schema(type="STRING", description="Maximum budget mentioned by customer (e.g., '1 Crore')"),
        },
        required=[],
    ),
)

BOOK_APPOINTMENT_DECLARATION = types.FunctionDeclaration(
    name="book_appointment",
    description="Book a site visit or callback appointment for the customer. Call this when the customer explicitly requests to schedule a site visit or a callback at a specific date and time. Confirm the details with the customer before calling this function.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "customer_name": types.Schema(type="STRING", description="Customer's full name"),
            "customer_phone": types.Schema(type="STRING", description="Customer's phone number"),
            "appointment_type": types.Schema(type="STRING", description="Type of appointment: 'site_visit' or 'callback'"),
            "property_of_interest": types.Schema(type="STRING", description="Property or project name they are interested in (if any)"),
            "preferred_date": types.Schema(type="STRING", description="Preferred date for the appointment in YYYY-MM-DD format (e.g., '2026-04-25')"),
            "preferred_time": types.Schema(type="STRING", description="Preferred time for the appointment in HH:MM format (e.g., '14:30')"),
            "notes": types.Schema(type="STRING", description="Any additional notes about the appointment (e.g., 'wants to see 2BHK and 3BHK options')"),
        },
        required=["customer_name", "customer_phone", "appointment_type", "preferred_date", "preferred_time"],
    ),
)

CHECK_AVAILABILITY_DECLARATION = types.FunctionDeclaration(
    name="check_appointment_availability",
    description="Check if there are any scheduling conflicts for a given date, time, or property. Call this BEFORE booking an appointment to see if the requested slot is already taken or if there are other appointments at the same time/property. Returns information about existing appointments that might conflict.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "date": types.Schema(type="STRING", description="Date to check in YYYY-MM-DD format"),
            "property_name": types.Schema(type="STRING", description="Property name to check for existing appointments"),
        },
        required=["date"],
    ),
)

TRIGGER_VOICE_CALL_DECLARATION = types.FunctionDeclaration(
    name="trigger_voice_call",
    description="Trigger an outbound voice call to the customer when they are interested and want to speak by phone. Use this after qualifying the lead on WhatsApp.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "phone": types.Schema(type="STRING", description="Customer's phone number in international format (e.g., +919876543210)"),
            "contact_id": types.Schema(type="STRING", description="Optional CRM contact ID or identifier"),
            "reason": types.Schema(type="STRING", description="Why the call should be triggered, e.g. interested_lead or requested_callback"),
        },
        required=["phone"],
    ),
)

SEND_APPOINTMENT_REMINDER_DECLARATION = types.FunctionDeclaration(
    name="send_appointment_reminder",
    description="Send an appointment reminder via WhatsApp to the customer. Call this when the customer has an upcoming appointment and wants a reminder. This helps reduce no-shows.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "phone": types.Schema(type="STRING", description="Customer's phone number in international format (e.g., +919876543210)"),
            "name": types.Schema(type="STRING", description="Customer's name"),
            "appointment_type": types.Schema(type="STRING", description="Type of appointment: 'site_visit' or 'callback'"),
            "appointment_date": types.Schema(type="STRING", description="Appointment date in YYYY-MM-DD format"),
            "appointment_time": types.Schema(type="STRING", description="Appointment time in HH:MM format"),
            "property_name": types.Schema(type="STRING", description="Property name for site visits (optional)"),
        },
        required=["phone", "name", "appointment_type", "appointment_date", "appointment_time"],
    ),
)

SEND_PROPERTY_PACK_DECLARATION = types.FunctionDeclaration(
    name="send_property_pack",
    description="Send a curated list of properties to the customer via WhatsApp. Call this when the customer wants to see property options or before a site visit.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "phone": types.Schema(type="STRING", description="Customer's phone number in international format (e.g., +919876543210)"),
            "name": types.Schema(type="STRING", description="Customer's name"),
            "property_ids": types.Schema(type="ARRAY", items=types.Schema(type="STRING", description="Property IDs or names to include in the pack"), description="List of property identifiers to send"),
        },
        required=["phone", "name", "property_ids"],
    ),
)

SEND_SURVEY_DECLARATION = types.FunctionDeclaration(
    name="send_followup_survey",
    description="Send a satisfaction survey to the customer via WhatsApp after a call. Call this when the conversation is complete and you want feedback.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "phone": types.Schema(type="STRING", description="Customer's phone number in international format (e.g., +919876543210)"),
            "name": types.Schema(type="STRING", description="Customer's name"),
            "call_sid": types.Schema(type="STRING", description="Twilio call SID for reference"),
        },
        required=["phone", "name", "call_sid"],
    ),
)

UPDATE_SENTIMENT_DECLARATION = types.FunctionDeclaration(
    name="update_sentiment",
    description="Update the customer's sentiment based on the conversation. Call this AFTER the call ends to record how the customer felt during the interaction. Evaluate their tone, satisfaction level, and overall experience.",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "phone": types.Schema(type="STRING", description="Customer's phone number in international format (e.g., +919876543210)"),
            "sentiment": types.Schema(
                type="STRING",
                enum=["positive", "negative", "neutral"],
                description="Overall sentiment of the conversation: 'positive' if customer was happy/satisfied, 'negative' if frustrated/unsatisfied, 'neutral' if indifferent/no strong opinion"
            ),
            "sentiment_description": types.Schema(type="STRING", description="Brief description of why this sentiment was chosen, mentioning key moments or phrases that influenced the evaluation"),
        },
        required=["phone", "sentiment", "sentiment_description"],
    ),
)

LIVE_TOOLS = [types.Tool(function_declarations=[
    SAVE_LEAD_DECLARATION,
    END_CALL_DECLARATION,
    REQUEST_WA_SUMMARY_DECLARATION,
    GET_PROPERTIES_DECLARATION,
    BOOK_APPOINTMENT_DECLARATION,
    CHECK_AVAILABILITY_DECLARATION,
    TRIGGER_VOICE_CALL_DECLARATION,
    SEND_APPOINTMENT_REMINDER_DECLARATION,
    SEND_PROPERTY_PACK_DECLARATION,
    SEND_SURVEY_DECLARATION,
    UPDATE_SENTIMENT_DECLARATION,
])]


async def analyze_sentiment_from_transcript(phone: str, transcripts: list[dict]) -> dict:
    """
    Analyze customer sentiment from conversation transcripts using Gemini's AI.
    This runs automatically after every call ends.
    
    Args:
        phone: Customer's phone number
        transcripts: List of dicts with 'speaker' (AI/User) and 'text' keys
        
    Returns:
        dict with 'sentiment' and 'sentiment_description'
    """
    if not transcripts:
        return {"sentiment": "neutral", "sentiment_description": "No conversation data available"}
    
    # Build conversation text for analysis
    conversation_text = ""
    for turn in transcripts:
        # Handle both formats: 'speaker' (AI/User) and 'role' (assistant/user)
        speaker = turn.get("speaker", "")
        text = turn.get("text", "").strip()
        if not text:
            text = turn.get("content", "").strip()  # Alternative field name
        if text:
            if speaker in ("AI", "assistant", "ai"):
                display_speaker = "AI Agent"
            elif speaker in ("User", "user"):
                display_speaker = "Customer"
            else:
                # Fallback to role field if speaker is not set
                role = turn.get("role", "user")
                display_speaker = "AI Agent" if role in ("assistant", "ai") else "Customer"
            conversation_text += f"{display_speaker}: {text}\n"
    
    # Extract key customer phrases (last 3000 chars for context window)
    recent_conversation = conversation_text[-3000:] if len(conversation_text) > 3000 else conversation_text
    
    # Prompt Gemini to analyze sentiment
    analysis_prompt = f"""Analyze the sentiment of this customer service conversation and determine if the customer was satisfied, frustrated, or neutral.

Conversation:
{recent_conversation}

Instructions:
1. Evaluate the customer's tone, satisfaction level, and key moments
2. Look for positive indicators: enthusiasm, gratitude, agreement, satisfaction
3. Look for negative indicators: frustration, complaints, disagreement, anger
4. If no strong feelings detected, classify as neutral

Respond in EXACTLY this JSON format (nothing else):
{{
  "sentiment": "positive" | "negative" | "neutral",
  "sentiment_description": "Brief 1-2 sentence explanation of why this sentiment was chosen, mentioning key phrases or moments"
}}

Focus on the customer's overall experience and tone throughout the conversation."""

    try:
        client = genai.Client(api_key=Config.GOOGLE_API_KEY)
        
        response = client.models.generate_content(
            model=Config.GEMINI_TEXT_MODEL,
            contents=analysis_prompt,
        )
        
        # Parse the JSON response
        response_text = response.text.strip()
        
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'\{[\s\S]*"sentiment"[^}]*\}', response_text)
        if json_match:
            import json
            sentiment_result = json.loads(json_match.group())
            return {
                "sentiment": sentiment_result.get("sentiment", "neutral"),
                "sentiment_description": sentiment_result.get("sentiment_description", "AI analysis complete"),
            }
        
        # Fallback if JSON parsing fails
        return {
            "sentiment": "neutral",
            "sentiment_description": f"Analysis response: {response_text[:200]}",
        }
        
    except Exception as e:
        logger = __import__('logging').getLogger(__name__)
        logger.error(f"Failed to analyze sentiment: {str(e)}")
        return {
            "sentiment": "neutral",
            "sentiment_description": f"Analysis failed: {str(e)}",
        }


class GeminiLiveSession:
    """Manages a live session with the Gemini Voice API."""

    def __init__(self):
        """Initialize the Gemini client and session state."""
        self.client = genai.Client(api_key=Config.GOOGLE_API_KEY)
        self.session = None
        self._session_context = None
        self.is_connected = False
        self._shutdown_event = asyncio.Event()  # Signal to stop receiving

    async def connect(
        self,
        max_retries: int = 3,
        response_modalities: list[str] | None = None,
        system_prompt: str | None = None,
    ):
        """
        Establish a connection to the Gemini Live API with retry logic.
        
        Args:
            max_retries: Number of connection attempts before giving up
            response_modalities: Optional list of modalities (e.g. ["TEXT"], ["AUDIO"])
            system_prompt: Optional custom system instructions prompt
            
        Returns:
            GeminiLiveSession: Self for chaining
        """
        # Use provided values or fall back to defaults
        modalities = response_modalities if response_modalities is not None else ["AUDIO"]
        sys_prompt = system_prompt if system_prompt is not None else Config.SYSTEM_PROMPT

        # Configure thinking based on setting
        config_params = {
            "response_modalities": modalities,
            "tools": LIVE_TOOLS,
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=Config.GEMINI_VOICE
                    )
                )
            ),
            "system_instruction": types.Content(
                parts=[types.Part(text=sys_prompt)]
            ),
            "realtime_input_config": types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=200,
                    silence_duration_ms=800,
                ),
            ),
        }
        
        # Only add thinking_config if it's not "none"
        if Config.GEMINI_THINKING.lower() != "none":
            config_params["thinking_config"] = types.ThinkingConfig(thinking_level=Config.GEMINI_THINKING)

        config = types.LiveConnectConfig(**config_params)
        
        logger.info(f"Connecting to Gemini: model={Config.GEMINI_MODEL}, voice={Config.GEMINI_VOICE}, thinking={Config.GEMINI_THINKING}")

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # client.aio.live.connect() returns an async context manager
                # We enter it manually so we can hold the session open
                self._session_context = self.client.aio.live.connect(
                    model=Config.GEMINI_MODEL,
                    config=config,
                )
                self.session = await asyncio.wait_for(
                    self._session_context.__aenter__(),
                    timeout=30.0,  # 30 second timeout for handshake
                )
                self.is_connected = True
                logger.info(f"Gemini Live session connected (attempt {attempt})")
                logger.debug(f"[GEMINI] Session connected successfully")
                return self
                
            except asyncio.TimeoutError:
                last_error = f"Handshake timed out (attempt {attempt}/{max_retries})"
                logger.warning(f"Gemini connection: {last_error}")
                # Clean up failed context
                self._session_context = None
            except Exception as e:
                last_error = f"Connection error (attempt {attempt}/{max_retries}): {e}"
                logger.warning(f"Gemini connection: {last_error}")
                self._session_context = None
            
            if attempt < max_retries:
                wait_time = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                logger.info(f"Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
        
        raise RuntimeError(f"Failed to connect to Gemini Live API after {max_retries} attempts: {last_error}")

    async def send_audio(self, pcm_audio_bytes: bytes):
        """
        Send realtime audio input to Gemini.
        
        Sends audio immediately - Gemini's AAD (Automatic Activity Detection)
        will detect pauses and generate responses automatically.
        
        Args:
            pcm_audio_bytes: PCM16 audio bytes (16kHz)
        """
        if not self.is_connected or self.session is None:
            raise RuntimeError("Not connected to Gemini Live API")
        
        # Send audio immediately - no buffering
        await self.session.send_realtime_input(
            audio=types.Blob(
                mime_type="audio/pcm;rate=16000",
                data=pcm_audio_bytes
            )
        )
        logger.debug(f"[GEMINI] Sent {len(pcm_audio_bytes)} bytes audio to Gemini")

    async def receive_audio(self):
        """
        Receive audio responses from Gemini as an async generator.
        
        Yields:
            tuple: (msg_type, data) where msg_type is one of:
                - "audio": PCM16 audio data (24kHz) from Gemini
                - "transcript": Model's spoken text
                - "user_transcript": User's spoken text
                - "tool_call": Function call object
                - "turn_complete": None (end of model turn)
                - "interrupted": True (user interrupted the model)
        """
        if self.session is None:
            raise RuntimeError("No session available")

        logger.debug(f"[GEMINI] receive_audio() started, waiting for responses...")
        
        while True:
            try:
                # Check if shutdown was requested
                if self._shutdown_event.is_set():
                    logger.debug(f"[GEMINI] Shutdown event set, stopping receive loop")
                    break

                async for response in self.session.receive():
                    # Check if shutdown was requested
                    if self._shutdown_event.is_set():
                        logger.debug(f"[GEMINI] Shutdown event set, stopping receive loop")
                        return
                        
                    # Handle tool calls from Gemini
                    if response.tool_call:
                        call_names = [fc.name for fc in response.tool_call.function_calls] if response.tool_call.function_calls else []
                        logger.info(f"[GEMINI_API] Received tool_call: {call_names}")
                        yield "tool_call", response.tool_call
                        continue

                    # The response has server_content with model_turn containing parts
                    if response.server_content:
                        if response.server_content.model_turn:
                            for part in response.server_content.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    audio_data = part.inline_data.data
                                    logger.info(f"[GEMINI_API] Received audio chunk: {len(audio_data)} bytes")
                                    yield "audio", audio_data
                                if part.text:
                                    text = part.text
                                    logger.info(f"[GEMINI_API] Received transcript: '{text}'")
                                    yield "transcript", text
                        if response.server_content.turn_complete:
                            logger.info(f"[GEMINI_API] Turn complete")
                            yield "turn_complete", None
                             
                        # Also check if Gemini yields the User's live transcript
                        if getattr(response.server_content, "interrupted", None):
                            logger.info(f"[GEMINI_API] Model interrupted by user")
                            yield "interrupted", True
                            
                        if getattr(response.server_content, "input_transcription", None):
                            if response.server_content.input_transcription.text:
                                user_text = response.server_content.input_transcription.text
                                logger.info(f"[GEMINI_API] User transcript: '{user_text}'")
                                yield "user_transcript", user_text
            except asyncio.CancelledError:
                # Task was cancelled - this is expected during cleanup
                return
            except Exception as e:
                if self._shutdown_event.is_set():
                    return
                error_str = str(e)
                # Detect ALL terminal websocket close codes - no point retrying
                if any(code in error_str for code in ("1000", "1001", "1006", "1008")) \
                        or "GoAway" in error_str \
                        or "session" in error_str.lower() \
                        or "close" in error_str.lower():
                    logger.warning(f"receive_audio: connection closed, stopping: {e}")
                    self.is_connected = False
                    return
                logger.error(f"receive_audio error: {e}")
                # Don't break - continue the loop to keep connection alive
                await asyncio.sleep(0.5)

    async def send_text(self, text: str):
        """
        Send text input to Gemini (for testing or text-based interaction).
        
        Args:
            text: Text message to send
        """
        if not self.is_connected or self.session is None:
            raise RuntimeError("Not connected to Gemini Live API")
        
        logger.debug(f"[GEMINI] send_text: '{text}'")
        
        # CRITICAL: Use send_realtime_input (NOT send_client_content)
        # send_client_content puts session in "conversational turns" mode
        # which breaks subsequent send_realtime_input(audio=...) calls
        await self.session.send_realtime_input(text=text)
        logger.debug(f"[GEMINI] send_text: completed, waiting for audio response...")

    async def send_tool_response(self, function_responses: list):
        """
        Send tool/function response back to Gemini after executing a tool call.
        
        Args:
            function_responses: List of dicts with 'name', 'id', and 'response' keys
        """
        if not self.is_connected or self.session is None:
            raise RuntimeError("Not connected to Gemini Live API")
        
        # Pass function_responses directly 
        await self.session.send_tool_response(function_responses=function_responses)

    async def close(self):
        """Close the Gemini Live session gracefully."""
        # Signal receive_audio to stop gracefully
        self._shutdown_event.set()
        
        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error closing Gemini session: {e}")
        self.is_connected = False
        self.session = None
        self._session_context = None
        
        # Reset shutdown event for potential reuse
        self._shutdown_event.clear()

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


class GeminiManager:
    """
    Manages multiple Gemini sessions for concurrent calls.
    Each call gets its own Gemini session.
    """

    def __init__(self):
        """Initialize the Gemini manager."""
        self.active_sessions: dict[str, GeminiLiveSession] = {}

    async def create_session(self, call_sid: str, system_prompt: str | None = None) -> GeminiLiveSession:
        """
        Create a new Gemini session for a call.
        
        Args:
            call_sid: Unique call identifier
            system_prompt: Optional custom system instructions prompt
            
        Returns:
            GeminiLiveSession: The new session
        """
        session = GeminiLiveSession()
        await session.connect(system_prompt=system_prompt)
        self.active_sessions[call_sid] = session
        return session

    async def close_session(self, call_sid: str):
        """
        Close a Gemini session for a call.
        
        Args:
            call_sid: Call identifier
        """
        if call_sid in self.active_sessions:
            await self.active_sessions[call_sid].close()
            del self.active_sessions[call_sid]

    def get_session(self, call_sid: str) -> GeminiLiveSession | None:
        """
        Get an active session by call SID.
        
        Args:
            call_sid: Call identifier
            
        Returns:
            GeminiLiveSession or None if not found
        """
        return self.active_sessions.get(call_sid)

    @property
    def active_count(self) -> int:
        """Get the number of active sessions."""
        return len(self.active_sessions)


# Global Gemini manager instance
gemini_manager = GeminiManager()
