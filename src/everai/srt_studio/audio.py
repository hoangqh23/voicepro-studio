"""Sample-counted adaptive SRT fitting and sequential timeline assembly."""
from array import array
from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import wave

from .model import digest, MAX_MS
try:
    from ..client import EverAICancelled
    from ..utils import ensure_ffmpeg, run_audio_command, AudioAssemblyError, publish_audio
except ImportError:
    from client import EverAICancelled
    from utils import ensure_ffmpeg, run_audio_command, AudioAssemblyError, publish_audio

RATE = 48000
WIDTH = 2
BUFFER = 65536
FIT_VERSION = 2
SILENCE_BLOCK = RATE // 100
EDGE_SILENCE = RATE * 60 // 1000
INTERNAL_SILENCE = RATE * 180 // 1000


class FitError(ValueError):
    """Source is retained and needs a different cue window/fit policy."""
    def __init__(self, message, source_frames=None):
        super().__init__(message)
        self.source_frames = source_frames


def check_cancel(cancel):
    if cancel.is_set():
        raise EverAICancelled('Đã dừng tác vụ SRT.')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + uuid.uuid4().hex + '.part')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def durable_replace(temporary, target):
    with Path(temporary).open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def run_ffmpeg(arguments, cancel, timeout=180):
    check_cancel(cancel)
    ffmpeg = ensure_ffmpeg()
    if not ffmpeg:
        raise AudioAssemblyError('Thiếu FFmpeg để xử lý SRT.')
    result = run_audio_command([str(ffmpeg), '-y', '-hide_banner', '-loglevel', 'error',
                               *map(str, arguments)], cancel_event=cancel, timeout=timeout)
    if result.returncode:
        raise AudioAssemblyError('Không xử lý được audio SRT bằng FFmpeg. Audio nguồn được giữ lại.')
    check_cancel(cancel)


def wave_frames(path):
    try:
        with wave.open(str(path), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, WIDTH, RATE, 'NONE'):
                raise ValueError('PCM SRT phải mono 48 kHz/16-bit.')
            frames = audio.getnframes()
            # Detect truncated data without loading a whole cue into memory.
            audio.setpos(max(0, frames - 1))
            if not frames or len(audio.readframes(1)) != WIDTH:
                raise ValueError('PCM SRT rỗng hoặc thiếu dữ liệu.')
            return frames
    except (wave.Error, EOFError) as error:
        raise ValueError('File PCM SRT không hợp lệ.') from error


@contextmanager
def decoded(raw, cancel, parent):
    with tempfile.TemporaryDirectory(prefix='.srt-decode-', dir=parent) as temporary:
        pcm = Path(temporary) / 'raw.wav'
        run_ffmpeg(['-i', raw, '-vn', '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le', pcm], cancel)
        wave_frames(pcm)
        yield pcm


def source_frames(raw, cancel):
    with decoded(raw, cancel, Path(raw).parent) as pcm:
        return wave_frames(pcm)


def zeros(writer, frames, cancel):
    if frames < 0:
        raise ValueError('Cue SRT chồng nhau.')
    block = b'\0' * (BUFFER * WIDTH)
    while frames:
        check_cancel(cancel)
        count = min(frames, BUFFER)
        writer.writeframesraw(block[:count * WIDTH])
        frames -= count


def copy_pcm(source, writer, frames, cancel):
    with wave.open(str(source), 'rb') as audio:
        remaining = frames
        while remaining:
            check_cancel(cancel)
            chunk = audio.readframes(min(remaining, BUFFER))
            if not chunk or len(chunk) % WIDTH:
                raise AudioAssemblyError('PCM cue SRT thiếu dữ liệu.')
            writer.writeframesraw(chunk)
            remaining -= len(chunk) // WIDTH


def open_writer(path):
    writer = wave.open(str(path), 'wb')
    writer.setnchannels(1)
    writer.setsampwidth(WIDTH)
    writer.setframerate(RATE)
    return writer


def tempo_filter(ratio):
    filters = []
    while ratio > 2:
        filters.append('atempo=2')
        ratio /= 2
    filters.append(f'atempo={ratio:.12f}')
    return ','.join(filters)


def _peak(source, cancel):
    peak = 0
    with wave.open(str(source), 'rb') as reader:
        while True:
            check_cancel(cancel)
            content = reader.readframes(BUFFER)
            if not content:
                break
            samples = array('h')
            samples.frombytes(content)
            if samples:
                peak = max(peak, max(abs(value) for value in samples))
    return peak


def _speech_segments(source, cancel):
    """Return conservative voiced ranges while capping long internal silence."""
    total = wave_frames(source)
    peak = _peak(source, cancel)
    if not peak:
        return [(0, total)]
    threshold = max(16, round(peak * .01))
    active = []
    with wave.open(str(source), 'rb') as reader:
        position = 0
        while position < total:
            check_cancel(cancel)
            content = reader.readframes(min(SILENCE_BLOCK, total - position))
            samples = array('h')
            samples.frombytes(content)
            if samples and max(abs(value) for value in samples) >= threshold:
                active.append((position, position + len(samples)))
            position += len(samples)
    if not active:
        return [(0, total)]
    low = max(0, active[0][0] - EDGE_SILENCE)
    high = min(total, active[-1][1] + EDGE_SILENCE)
    segments = []
    segment_start = low
    previous_end = active[0][1]
    half = INTERNAL_SILENCE // 2
    for start, end in active[1:]:
        gap = start - previous_end
        if gap > INTERNAL_SILENCE:
            segments.append((segment_start, previous_end + half))
            segment_start = start - half
        previous_end = end
    segments.append((segment_start, high))
    return [(start, end) for start, end in segments if end > start]


def _write_segments(source, target, segments, cancel):
    with wave.open(str(source), 'rb') as reader, open_writer(target) as writer:
        for start, end in segments:
            reader.setpos(start)
            remaining = end - start
            while remaining:
                check_cancel(cancel)
                content = reader.readframes(min(BUFFER, remaining))
                if not content or len(content) % WIDTH:
                    raise AudioAssemblyError('PCM cue SRT thiếu dữ liệu khi cắt khoảng lặng.')
                writer.writeframesraw(content)
                remaining -= len(content) // WIDTH
    return wave_frames(target)


def fit_cue(raw, target, window_frames, max_tempo, cancel):
    """Trim silence and speed up within policy; overflow is retained, never rejected."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not 0 < window_frames <= MAX_MS * 48 or not math.isfinite(max_tempo) or not 1 <= max_tempo <= 4:
        raise ValueError('Cửa sổ hoặc giới hạn tăng tốc SRT không hợp lệ.')
    with tempfile.TemporaryDirectory(prefix='.srt-fit-', dir=target.parent) as temporary:
        directory = Path(temporary)
        with decoded(raw, cancel, directory) as original:
            source_duration = wave_frames(original)
            segments = _speech_segments(original, cancel)
            trimmed = directory / 'trimmed.wav'
            trimmed_duration = _write_segments(original, trimmed, segments, cancel)
            ratio = 1.
            pcm, actual = trimmed, trimmed_duration
            if trimmed_duration > window_frames:
                # Small headroom reduces atempo rounding errors, within the selected limit.
                ratio = min(max_tempo, trimmed_duration / max(1, window_frames - min(480, window_frames // 100)))
                for attempt in range(3):
                    candidate = directory / f'tempo-{attempt}.wav'
                    try:
                        run_ffmpeg(['-i', trimmed, '-af', tempo_filter(ratio), '-c:a', 'pcm_s16le', candidate], cancel)
                        actual = wave_frames(candidate)
                        pcm = candidate
                    except (AudioAssemblyError, ValueError):
                        # A filter failure must not strand already-created/paid speech.
                        ratio, pcm, actual = 1., trimmed, trimmed_duration
                        break
                    if actual <= window_frames:
                        break
                    next_ratio = ratio * actual / window_frames * 1.002
                    if next_ratio > max_tempo:
                        if ratio >= max_tempo - 1e-9:
                            break
                        next_ratio = max_tempo
                    ratio = next_ratio
            result = directory / 'fitted.wav'
            with open_writer(result) as writer:
                copy_pcm(pcm, writer, actual, cancel)
            if wave_frames(result) != actual:
                raise AudioAssemblyError('Số mẫu PCM cue không khớp phần lời đã căn.')
            check_cancel(cancel)
            durable_replace(result, target)
    return dict(file=str(target), frames=actual, source_frames=source_duration,
                trimmed_frames=trimmed_duration, trimmed_silence_frames=source_duration - trimmed_duration,
                target_frames=window_frames, speech_frames=actual, tempo=ratio,
                overflow_frames=max(0, actual - window_frames), sha256=file_hash(target),
                version=FIT_VERSION)


def fit_identity(raw_hash, window_frames, policy, max_tempo):
    return digest(dict(raw=raw_hash, frames=window_frames, policy=policy,
                       max_tempo=max_tempo, rate=RATE, width=WIDTH, version=FIT_VERSION))


def check_space(parent, frames, output_format):
    if not 0 < frames <= MAX_MS * 48 or frames * WIDTH + 4096 >= 2**32:
        raise ValueError('Timeline SRT vượt giới hạn WAV/6 giờ.')
    # PCM temp plus published PCM/encoded audio, and a small working reserve.
    required = frames * WIDTH * 2 + 16 * 1024 * 1024
    if shutil.disk_usage(parent).free < required:
        raise OSError('Không đủ dung lượng trống để xuất timeline SRT.')


def timeline_frames(cues):
    """Calculate the adaptive sequential result length from fit metadata."""
    ordered = sorted(cues, key=lambda c: (c['start_ms'], c['source_order']))
    end = max(c['end_ms'] for c in ordered) * 48
    cursor = 0
    for cue in ordered:
        fit = cue.get('fit') or {}
        start = max(cursor, int(fit.get('placed_start_frame', cue['start_ms'] * 48)))
        cursor = start + int(fit.get('speech_frames', (cue['end_ms'] - cue['start_ms']) * 48))
        end = max(end, cursor)
    return end


def assemble(cues, destination, output_format, cancel):
    """One sequential PCM pass; speech may drift but is never overlapped or cut."""
    if output_format not in ('wav', 'mp3'):
        raise ValueError('Chỉ hỗ trợ WAV hoặc MP3.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(cues, key=lambda c: (c['start_ms'], c['source_order']))
    nominal_frames = max(c['end_ms'] for c in ordered) * 48
    cursor = 0
    frames = nominal_frames
    for cue in ordered:
        fitted = Path(cue['fit_path'])
        actual = wave_frames(fitted)
        fit = cue.get('fit') or {}
        expected = fit.get('speech_frames')
        if expected is not None and actual != expected:
            raise AudioAssemblyError('Audio cue đã thay đổi; hãy căn lại timeline.')
        start = max(cursor, int(fit.get('placed_start_frame', cue['start_ms'] * 48)))
        cursor = start + actual
        frames = max(frames, cursor)
    check_space(destination.parent, frames, output_format)
    with tempfile.TemporaryDirectory(prefix='.srt-export-', dir=destination.parent) as temporary:
        directory = Path(temporary)
        pcm = directory / 'timeline.wav'
        cursor = 0
        with open_writer(pcm) as writer:
            for cue in ordered:
                fitted = Path(cue['fit_path'])
                actual = wave_frames(fitted)
                fit = cue.get('fit') or {}
                start = max(cursor, int(fit.get('placed_start_frame', cue['start_ms'] * 48)))
                zeros(writer, start - cursor, cancel)
                copy_pcm(fitted, writer, actual, cancel)
                cursor = start + actual
            if frames > cursor:
                zeros(writer, frames - cursor, cancel)
        if wave_frames(pcm) != frames:
            raise AudioAssemblyError('Thời lượng timeline SRT không khớp.')
        result = pcm
        if output_format == 'mp3':
            result = directory / 'timeline.mp3'
            run_ffmpeg(['-i', pcm, '-c:a', 'libmp3lame', '-b:a', '128k', result], cancel,
                       timeout=max(180, frames / RATE * .2))
            # Verify decoded MP3 sample count; encoders/decoders may expose padding.
            with decoded(result, cancel, directory) as verified:
                if abs(wave_frames(verified) - frames) > RATE * .05:
                    raise AudioAssemblyError('MP3 xuất ra lệch thời lượng timeline quá 50 ms.')
        check_cancel(cancel)
        with result.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        final = publish_audio(result, destination)
        return final
