"""Isolated adapter for the supported external TTS provider."""
import math
import os
from pathlib import Path
import tempfile
import threading
from .capcut import CapCutClient, catalog_voices
from .saydi import SaydiClient, VOICE_ID

try:
    from ..client import EverAIError, EverAICancelled
    from ..utils import ensure_ffmpeg, run_audio_command
except ImportError:
    from client import EverAIError, EverAICancelled
    from utils import ensure_ffmpeg, run_audio_command

ROOT = Path(__file__).resolve().parent
VOICES = [*catalog_voices(), dict(engine='saydi', voiceId=VOICE_ID, label='HN - Ngọc Huyền',
                                 lang='vi', region='Miền Bắc')]
PROVIDERS = {'capcut', 'saydi'}
REMOVED_PROVIDERS = {'elevenlabs', 'edge'}


def split_voice_id(value):
    provider, separator, voice = value.partition('|')
    known = PROVIDERS | REMOVED_PROVIDERS
    return (provider, voice) if separator and provider in known else ('everai', value)


def studio_voices():
    return [dict(id=v['engine'] + '|' + v['voiceId'], name=v['label'],
                 display=f"[{v['engine'].title()}] {v['label']} · {v['lang']} · {v['voiceId']}",
                 region=v.get('region') or '', lang=v['lang'], engine=v['engine']) for v in VOICES]


class TTSService:
    def __init__(self, cancel_event=None, saydi_token=None):
        self.cancel_event = cancel_event or threading.Event()
        self.saydi_token = (saydi_token or os.environ.get('SAYDI_ACCESS_TOKEN', '')).strip()

    def preflight(self, provider):
        if provider in REMOVED_PROVIDERS:
            raise ValueError('Dịch vụ giọng đọc cũ không còn được hỗ trợ. Hãy chọn Voice Pro hoặc CapCut.')
        if provider not in PROVIDERS:
            raise ValueError('Provider TTS không hợp lệ.')
        if not ensure_ffmpeg():
            raise ValueError('Thiếu FFmpeg để xử lý audio TTS.')

    def synthesize(self, text, output, voice_id, speed=1.0, proxy=None, model_id=None):
        provider, voice = split_voice_id(voice_id)
        self.preflight(provider)
        if not math.isfinite(speed) or not 0.5 <= speed <= 2:
            raise ValueError('Tốc độ phải nằm trong khoảng 0.5–2.0.')
        if not any(v['engine'] == provider and v['voiceId'] == voice for v in VOICES):
            raise ValueError('Giọng không thuộc provider đã chọn.')
        if self.cancel_event.is_set():
            raise EverAICancelled('Đã dừng tác vụ.')
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='tts-', dir=output.parent) as temporary:
            target = Path(temporary) / 'audio.mp3'
            if provider == 'saydi':
                SaydiClient(self.saydi_token).synthesize(text, voice, target)
            else:
                CapCutClient(self.cancel_event).synthesize(text, voice, target)
            if not target.is_file() or target.stat().st_size < 128:
                raise EverAIError('Provider trả về audio rỗng.')
            normalized = Path(temporary) / 'normalized.mp3'
            arguments = [str(ensure_ffmpeg()), '-y', '-v', 'error', '-i', str(target)]
            if abs(speed - 1) > 0.001:
                arguments += ['-af', f'atempo={speed}']
            arguments += ['-ar', '44100', '-ac', '1', '-codec:a', 'libmp3lame',
                          '-b:a', '128k', str(normalized)]
            conversion = run_audio_command(arguments, cancel_event=self.cancel_event, timeout=120)
            if conversion.returncode or not normalized.is_file():
                raise EverAIError('Không chuẩn hóa được audio của provider.')
            if self.cancel_event.is_set():
                raise EverAICancelled('Đã dừng tác vụ.')
            os.replace(normalized, output)
        return output

