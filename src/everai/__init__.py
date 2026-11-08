"""
EverAI TTS Package - Professional Long-Text Audio Generator.
"""

from .voices import EVERAI_VOICES, DEFAULT_VOICE_ID, get_voice_by_id
from .client import EverAIClient, EverAIError, EverAIQuotaError
from .engine import EverAITTSEngine

__all__ = [
    "EVERAI_VOICES",
    "DEFAULT_VOICE_ID",
    "get_voice_by_id",
    "EverAIClient",
    "EverAIError",
    "EverAIQuotaError",
    "EverAITTSEngine",
]
