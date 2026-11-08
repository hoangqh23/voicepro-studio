"""Real offline PCM/FFmpeg tests for sample placement, fit and encoded exports."""
import math
from pathlib import Path
import struct
import threading
import tempfile
import tracemalloc
import wave

import pytest

from src.everai.srt_studio import audio
from src.everai.client import EverAICancelled


def tone(path, frames=4800, value=12000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with audio.open_writer(path) as writer:
        # Silence-free tone starts at sample zero, making placement observable.
        values = [round(value * math.cos(2 * math.pi * 400 * i / audio.RATE)) for i in range(frames)]
        writer.writeframes(struct.pack('<' + 'h' * frames, *values))
    return path


def test_short_cue_is_not_padded_and_assembly_places_exact_pcm_positions(tmp_path):
    cancel = threading.Event()
    raw = tone(tmp_path / 'raw.wav')
    fitted = tmp_path / 'fit.wav'
    info = audio.fit_cue(raw, fitted, 9600, 1.35, cancel)
    assert info['source_frames'] == 4800 and info['speech_frames'] == 4800
    assert info['tempo'] == 1
    assert audio.wave_frames(fitted) == 4800
    cues = [dict(start_ms=100, end_ms=300, source_order=0, fit_path=fitted),
            dict(start_ms=500, end_ms=700, source_order=1, fit_path=fitted)]
    result = audio.assemble(cues, tmp_path / 'out.wav', 'wav', cancel)
    with wave.open(str(result), 'rb') as stream:
        assert stream.getnframes() == 700 * 48
        samples = struct.unpack('<' + 'h' * stream.getnframes(), stream.readframes(stream.getnframes()))
    assert not any(samples[:4800])
    assert samples[4800] == 12000 and samples[24000] == 12000
    assert not any(samples[9600:24000])
    assert not any(samples[28800:])


def test_long_cue_fit_uses_real_ffmpeg_and_remeasures(tmp_path):
    cancel = threading.Event()
    raw = tone(tmp_path / 'long.wav', 60000)
    info = audio.fit_cue(raw, tmp_path / 'fit.wav', 48000, 1.35, cancel)
    assert 1.25 <= info['tempo'] <= 1.35
    assert info['speech_frames'] <= 48000
    assert audio.wave_frames(tmp_path / 'fit.wav') == info['speech_frames']
    assert audio.wave_frames(raw) == 60000


def test_fit_trims_edge_silence_and_caps_long_internal_pause(tmp_path):
    raw = tmp_path / 'pauses.wav'
    voiced = struct.pack('<' + 'h' * 9600, *([12000] * 9600))
    with audio.open_writer(raw) as writer:
        writer.writeframes(b'\0' * (24000 * 2))
        writer.writeframes(voiced)
        writer.writeframes(b'\0' * (24000 * 2))
        writer.writeframes(voiced)
        writer.writeframes(b'\0' * (24000 * 2))
    info = audio.fit_cue(raw, tmp_path / 'fit.wav', 96000, 1.35, threading.Event())
    assert info['source_frames'] == 91200
    assert 32000 <= info['trimmed_frames'] <= 35000
    assert info['speech_frames'] == info['trimmed_frames']
    assert info['trimmed_silence_frames'] > 55000


def test_fit_limit_keeps_full_audio_and_reports_overflow_without_touching_source(tmp_path):
    raw = tone(tmp_path / 'raw.wav', 60000)
    before = audio.file_hash(raw)
    info = audio.fit_cue(raw, tmp_path / 'fit.wav', 12000, 1.35, threading.Event())
    assert audio.file_hash(raw) == before
    assert (tmp_path / 'fit.wav').is_file()
    assert info['tempo'] == pytest.approx(1.35, rel=.02)
    assert info['speech_frames'] == audio.wave_frames(tmp_path / 'fit.wav')
    assert info['overflow_frames'] == info['speech_frames'] - 12000 > 0


def test_force_fit_chains_tempo_and_never_exceeds_limit(tmp_path):
    raw = tone(tmp_path / 'raw.wav', 72000)
    info = audio.fit_cue(raw, tmp_path / 'fit.wav', 24000, 4., threading.Event())
    assert 3 <= info['tempo'] <= 4
    assert info['speech_frames'] <= 24000
    assert audio.wave_frames(tmp_path / 'fit.wav') == info['speech_frames']


def test_atempo_overrun_does_not_cut_tail(tmp_path, monkeypatch):
    raw = tone(tmp_path / 'raw.wav', 60000)
    original = audio.run_ffmpeg
    def wrong_duration(args, cancel, **kwargs):
        if '-af' in args:
            tone(args[-1], 60000)
        else:
            original(args, cancel, **kwargs)
    monkeypatch.setattr(audio, 'run_ffmpeg', wrong_duration)
    info = audio.fit_cue(raw, tmp_path / 'fit.wav', 48000, 1.35, threading.Event())
    assert audio.wave_frames(tmp_path / 'fit.wav') == 60000
    assert info['speech_frames'] == 60000
    assert info['overflow_frames'] == 12000


def test_mp3_is_encoded_and_decode_duration_checked(tmp_path):
    cancel = threading.Event()
    fitted = tone(tmp_path / 'fit.wav', 24000)
    result = audio.assemble([dict(start_ms=100, end_ms=600, source_order=0, fit_path=fitted)],
                            tmp_path / 'timeline.mp3', 'mp3', cancel)
    assert result.read_bytes()[:3] == b'ID3'
    with audio.decoded(result, cancel, tmp_path) as pcm:
        assert abs(audio.wave_frames(pcm) - 28800) <= 2400


def test_cancel_and_disk_limit_never_publish(tmp_path, monkeypatch):
    fitted = tone(tmp_path / 'fit.wav')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(EverAICancelled):
        audio.assemble([dict(start_ms=0, end_ms=100, source_order=0, fit_path=fitted)],
                       tmp_path / 'out.wav', 'wav', cancel)
    assert not (tmp_path / 'out.wav').exists()
    monkeypatch.setattr(audio.shutil, 'disk_usage', lambda p: type('Space', (), dict(free=0))())
    with pytest.raises(OSError, match='dung lượng'):
        audio.check_space(tmp_path, 4800, 'wav')
    with pytest.raises(ValueError, match='giới hạn'):
        audio.check_space(tmp_path, 7 * 3600 * 48000, 'wav')


def test_streaming_assembly_keeps_buffer_bounded(tmp_path, monkeypatch):
    fitted = tone(tmp_path / 'fit.wav')
    recorded = []
    original = audio.zeros
    def record(writer, frames, cancel):
        recorded.append(frames)
        original(writer, frames, cancel)
    monkeypatch.setattr(audio, 'zeros', record)
    result = audio.assemble([dict(start_ms=10000, end_ms=10100, source_order=0, fit_path=fitted)],
                            tmp_path / 'long.wav', 'wav', threading.Event())
    assert audio.wave_frames(result) == 10100 * 48
    assert recorded == [480000]
    assert not list(tmp_path.glob('.srt-*'))


def test_ten_thousand_cue_placement_is_exact_and_memory_bounded(tmp_path):
    fitted = tone(tmp_path / 'one-ms.wav', frames=48)
    cues = [dict(start_ms=i*2, end_ms=i*2+1, source_order=i, fit_path=fitted) for i in range(10000)]
    tracemalloc.start()
    try:
        result = audio.assemble(cues, tmp_path / 'ten-thousand.wav', 'wav', threading.Event())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    with wave.open(str(result), 'rb') as reader:
        assert reader.getnframes() == 19999 * 48
        reader.setpos(9999 * 2 * 48)
        assert struct.unpack('<h', reader.readframes(1))[0] == 12000
        reader.setpos(48)
        assert reader.readframes(48) == b'\0' * 96


def test_six_hour_timeline_streams_on_disk_without_large_memory_allocation(tmp_path):
    # TemporaryDirectory removes this ~2 GB synthetic file immediately after verification.
    with tempfile.TemporaryDirectory(prefix='everai-srt-six-hour-') as temporary:
        root = Path(temporary)
        frames = 6 * 3600 * 48000
        if audio.shutil.disk_usage(root).free < frames * 4 + 32 * 1024 * 1024:
            pytest.skip('Six-hour PCM test needs ~4.2 GB free scratch space')
        fitted = tone(tmp_path / 'one-ms.wav', frames=48)
        cues = [dict(start_ms=21600000-1, end_ms=21600000, source_order=0, fit_path=fitted)]
        tracemalloc.start()
        try:
            result = audio.assemble(cues, root / 'six-hour.wav', 'wav', threading.Event())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 8 * 1024 * 1024
        with wave.open(str(result), 'rb') as reader:
            assert reader.getnframes() == frames
            assert reader.readframes(1) == b'\0\0'
            reader.setpos(frames - 48)
            assert struct.unpack('<h', reader.readframes(1))[0] == 12000
