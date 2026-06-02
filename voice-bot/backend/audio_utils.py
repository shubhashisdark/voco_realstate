"""
Audio format conversion utilities for bridging Twilio (8kHz µ-law) and Gemini (16kHz/24kHz PCM).
"""

import base64
import struct
try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
    except ImportError:
        raise ImportError("The 'audioop' module is required. Install 'audioop-lts' for Python 3.13+.")


def mulaw_to_pcm(mulaw_bytes: bytes) -> bytes:
    """
    Convert mu-law encoded audio to PCM16.
    
    Args:
        mulaw_bytes: Raw mu-law encoded bytes (typically 8kHz)
        
    Returns:
        PCM16 audio data
    """
    return audioop.ulaw2lin(mulaw_bytes, 2)


def pcm_to_mulaw(pcm_bytes: bytes) -> bytes:
    """
    Convert PCM16 audio to mu-law encoded audio.
    
    Args:
        pcm_bytes: Raw PCM16 audio data
        
    Returns:
        Mu-law encoded audio bytes
    """
    return audioop.lin2ulaw(pcm_bytes, 2)


def resample_pcm(input_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    Resample PCM audio from one sample rate to another.
    
    Uses audioop.ratecv for efficient resampling.
    
    Args:
        input_bytes: PCM16 audio data
        from_rate: Source sample rate (e.g., 16000)
        to_rate: Target sample rate (e.g., 24000)
        
    Returns:
        Resampled PCM16 audio data
    """
    # ratecv args: (fragment, width, nchannels, inrate, outrate, state)
    # nchannels=1 for mono (phone audio), width=2 for 16-bit PCM
    resampled, _ = audioop.ratecv(input_bytes, 2, 1, from_rate, to_rate, None)
    return resampled


def twilio_to_gemini_audio(twilio_audio: bytes) -> bytes:
    """
    Convert audio from Twilio format to Gemini format.
    
    Pipeline: mu-law 8kHz → PCM16 8kHz → PCM16 16kHz
    
    Args:
        twilio_audio: Base64-decoded mu-law audio from Twilio Media Stream
        
    Returns:
        PCM16 audio at 16kHz for Gemini
    """
    # Step 1: Convert mu-law to PCM16
    pcm = mulaw_to_pcm(twilio_audio)
    
    # Step 2: Resample from 8kHz to 16kHz
    pcm_resampled = resample_pcm(pcm, 8000, 16000)
    
    return pcm_resampled


def gemini_to_twilio_audio(gemini_audio: bytes) -> bytes:
    """
    Convert audio from Gemini format to Twilio format.
    
    Pipeline: PCM16 24kHz → PCM16 16kHz → PCM16 8kHz → mu-law
    
    Args:
        gemini_audio: PCM16 audio from Gemini (24kHz)
        
    Returns:
        Mu-law encoded audio at 8kHz for Twilio Media Stream
    """
    # Step 1: 24kHz → 16kHz (3:2 ratio)
    intermediate, _ = audioop.ratecv(gemini_audio, 2, 1, 24000, 16000, None)
    
    # Step 2: 16kHz → 8kHz (2:1 ratio)
    resampled_data, _ = audioop.ratecv(intermediate, 2, 1, 16000, 8000, None)
    
    # Step 3: Convert PCM16 to mu-law
    mulaw = pcm_to_mulaw(resampled_data)
    
    return mulaw


def base64_decode_audio(base64_string: str) -> bytes:
    """
    Decode base64 audio string to raw bytes.
    
    Args:
        base64_string: Base64 encoded audio string
        
    Returns:
        Raw audio bytes
    """
    return base64.b64decode(base64_string)


def base64_encode_audio(raw_bytes: bytes) -> str:
    """
    Encode raw audio bytes to base64 string.
    
    Args:
        raw_bytes: Raw audio bytes
        
    Returns:
        Base64 encoded string
    """
    return base64.b64encode(raw_bytes).decode('utf-8')


def validate_audio_conversion_roundtrip():
    """
    Test: Convert mu-law → PCM → mu-law and verify fidelity.
    
    Returns:
        True if round-trip conversion is successful
    """
    # Generate test mu-law data (silence)
    test_data = bytes([128] * 160)  # 160 samples of silence
    
    # Round-trip: mu-law → PCM → mu-law
    pcm = mulaw_to_pcm(test_data)
    result = pcm_to_mulaw(pcm)
    
    # Check if we get back the original data
    return test_data == result


if __name__ == "__main__":
    # Run basic validation
    print("Testing audio conversion round-trip...")
    if validate_audio_conversion_roundtrip():
        print("[PASS] Round-trip conversion successful")
    else:
        print("[FAIL] Round-trip conversion failed")
    
    print("\nAudio utility module ready.")
    print("Supported conversions:")
    print("  - Twilio (mu-law 8kHz) -> Gemini (PCM 16kHz)")
    print("  - Gemini (PCM 24kHz) -> Twilio (mu-law 8kHz)")