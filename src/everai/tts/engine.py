"""Reuse Studio job persistence, license accounting and ordered assembly."""
try:
    from ..engine import EverAITTSEngine
    from ..client import EverAICancelled
except ImportError:
    from engine import EverAITTSEngine
    from client import EverAICancelled
from .service import TTSService, split_voice_id
from .srt import split_srt_chunks, assemble_timeline
from .saydi import CHUNK_LIMIT


class MultiProviderTTSEngine(EverAITTSEngine):
    def __init__(self, *, input_mode='auto', saydi_token=None, **kwargs):
        if input_mode not in ('auto', 'text'):
            raise ValueError('Chế độ nội dung TTS không hợp lệ.')
        self.input_mode = input_mode
        super().__init__(**kwargs)
        self.service = TTSService(cancel_event=self.cancel_event, saydi_token=saydi_token)
        self._configured_chunk_chars = self.max_chunk_chars
        self.srt_groups = []

    def process(self, *args, **kwargs):
        speaker = kwargs.get('speaker_id', args[2] if len(args) > 2 else '')
        provider, _ = split_voice_id(speaker)
        self.service.preflight(provider)
        self.max_chunk_chars = self._configured_chunk_chars
        if provider == 'saydi':
            self.max_chunk_chars = min(self.max_chunk_chars, CHUNK_LIMIT)
        return super().process(*args, **kwargs)

    def split_text(self, text):
        self.srt_groups = []
        if self.input_mode == 'text' or '-->' not in text:
            return super().split_text(text)
        chunks, self.srt_groups = split_srt_chunks(text, super().split_text)
        return chunks

    def _assemble_audio(self, results, target):
        if self.srt_groups:
            return assemble_timeline(results, self.srt_groups, target, self.cancel_event)
        return super()._assemble_audio(results, target)

    def _process_single_chunk(self, idx, chunk_text, total_chunks, output_chunk_path,
                              speaker_id, model_id, speed, pitch, volume, bitrate,
                              audio_type, has_configured_proxies):
        if self.cancel_event.is_set():
            raise EverAICancelled('Đã dừng tác vụ.')
        if self.license_manager is not None:
            self.license_manager.ensure_active()
        provider, _ = split_voice_id(speaker_id)
        direct = provider in ('capcut', 'saydi')
        proxy = None if direct else self.get_current_proxy()
        if not direct and has_configured_proxies and not proxy:
            raise ValueError('Chưa có proxy được kích hoạt.')
        self._status('rendering', chunk=idx, total=total_chunks)
        result = self.service.synthesize(chunk_text, output_chunk_path, speaker_id, speed, proxy, model_id)
        self.log(f'[TTS] Đã tạo đoạn {idx}/{total_chunks}.', 'SUCCESS')
        return result
