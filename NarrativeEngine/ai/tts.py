"""TTS client using ElevenLabs (human-quality, character voices).

Usage:
    await speak(text, voice_id)  — generate + play (cancels previous)
    stop()                       — stop current playback immediately

Configure via .env:
    ELEVEN_API_KEY   — your ElevenLabs API key (elevenlabs.io)
    TTS_VOICE_ID     — voice ID from elevenlabs.io/voice-lab
    TTS_MODEL        — eleven_multilingual_v2 (default) or eleven_turbo_v2_5

All env vars are read at *call time*, not at module import time, so
dotenv-loaded values (from ai/client.py's load_dotenv()) are always visible.
"""

import os
import tempfile

from elevenlabs.client import AsyncElevenLabs
from elevenlabs import VoiceSettings
import pygame.mixer

# ── Config ────────────────────────────────────────────────────────────────────

# Sentinel default for voice — callers may pass their own voice_id instead.
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam — deep narrator

_TMP = os.path.join(tempfile.gettempdir(), "narrativeengine_tts.mp3")

# Voice settings tuned for an expressive old-wizard narrator:
#   stability    — low value = more varied energy, less monotone
#   similarity   — preserve the voice's character across long sessions
#   style        — dramatic storytelling emphasis
_WIZARD_SETTINGS = VoiceSettings(
    stability=0.35,
    similarity_boost=0.85,
    style=0.60,
    use_speaker_boost=True,
)

# ── State ─────────────────────────────────────────────────────────────────────

_gen: int = 0      # incremented on each speak() call — stale audio is discarded
_ready: bool = False


# ── Internals ─────────────────────────────────────────────────────────────────

def _init() -> None:
    global _ready
    if not _ready:
        pygame.mixer.init()
        _ready = True


# ── Public API ────────────────────────────────────────────────────────────────

async def check_available() -> bool:
    """Lightweight API call to verify ElevenLabs is reachable with the configured key.

    Reads ELEVEN_API_KEY from the environment at call time (not import time)
    so dotenv-loaded values are always visible.
    Uses client.user.get() — no audio is generated, so it's fast and cheap.
    Returns True on success, False on any error (bad key, no internet, etc.).
    """
    api_key = os.getenv("ELEVEN_API_KEY", "")
    if not api_key:
        return False
    try:
        client = AsyncElevenLabs(api_key=api_key)
        await client.user.get()
        return True
    except Exception:
        return False


def stop() -> None:
    """Stop any currently playing TTS immediately."""
    if _ready:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()


async def speak(text: str, voice_id: str | None = None) -> None:
    """Generate TTS via ElevenLabs and play it.

    Reads ELEVEN_API_KEY, TTS_VOICE_ID, and TTS_MODEL from the environment
    at call time so dotenv values are always current.

    If a newer speak() call arrives while this one is generating audio,
    the generation-counter check causes this call to exit without playing.
    """
    api_key  = os.getenv("ELEVEN_API_KEY", "")
    model    = os.getenv("TTS_MODEL", "eleven_multilingual_v2")
    voice_id = voice_id or os.getenv("TTS_VOICE_ID", DEFAULT_VOICE_ID)

    global _gen
    _gen += 1
    my_gen = _gen

    _init()
    stop()  # cancel any currently playing audio

    try:
        client = AsyncElevenLabs(api_key=api_key)
        audio = b""
        async for chunk in client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=model,
            voice_settings=_WIZARD_SETTINGS,
            output_format="mp3_44100_128",
        ):
            audio += chunk

        if _gen != my_gen:
            # A newer speak() call already claimed the slot — discard our audio.
            return

        with open(_TMP, "wb") as f:
            f.write(audio)

        pygame.mixer.music.load(_TMP)
        pygame.mixer.music.play()  # non-blocking; audio plays in the background

    except Exception as exc:
        raise RuntimeError(f"TTS unavailable: {exc}") from exc
