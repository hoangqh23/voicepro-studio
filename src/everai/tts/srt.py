"""SRT parsing and audio-only timeline assembly for provider TTS."""
import re
from pathlib import Path
import tempfile

try:
    from ..utils import ensure_ffmpeg, concat_mp3_files, run_audio_command
    from ..client import EverAIError, EverAICancelled
except ImportError:
    from utils import ensure_ffmpeg, concat_mp3_files, run_audio_command
    from client import EverAIError, EverAICancelled

TIMING = re.compile(r'^(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*'
                    r'(\d+):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$')


def parse_srt(text):
    cues = []
    for block in re.split(r'\n\s*\n', text.lstrip('\ufeff').replace('\r\n', '\n').strip()):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines.pop(0)
        match = TIMING.match(lines[0].strip()) if lines else None
        if not match:
            raise ValueError('Mốc thời gian SRT không hợp lệ.')
        values = list(map(int, match.groups()))
        def seconds(offset):
            h, m, s, ms = values[offset:offset+4]
            if m >= 60 or s >= 60:
                raise ValueError('Mốc thời gian SRT không hợp lệ.')
            return h*3600 + m*60 + s + ms/1000
        start, end = seconds(0), seconds(4)
        body = re.sub(r'<[^>]+>', '', ' '.join(lines[1:])).strip()
        if end <= start or not body:
            raise ValueError('Cue SRT trống hoặc thời lượng không hợp lệ.')
        cues.append(dict(start=start, end=end, text=body))
    return cues


def split_srt_chunks(text, split_text):
    """Use the same per-cue boundaries for rendering and UI estimates."""
    chunks, groups = [], []
    for cue in parse_srt(text):
        first = len(chunks)
        chunks.extend(split_text(cue['text']))
        groups.append(dict(cue, indices=list(range(first, len(chunks)))))
    return chunks, groups


def assemble_timeline(results, groups, target, cancel_event):
    """Fit each cue only when too long; render gaps/overlaps on original timestamps."""
    ffmpeg = str(ensure_ffmpeg())
    def run(args):
        if cancel_event.is_set():
            raise EverAICancelled('Đã dừng tác vụ.')
        result = run_audio_command([ffmpeg, '-y', '-hide_banner', *args],
                                   cancel_event=cancel_event, timeout=180)
        if result.returncode:
            raise EverAIError('Không ghép được timeline SRT.')
        return result
    with tempfile.TemporaryDirectory(prefix='srt-', dir=Path(target).parent) as temporary:
        directory = Path(temporary)
        stems = []
        # Batches keep Windows command lines and FFmpeg's input count bounded.
        for batch_start in range(0, len(groups), 24):
            args, filters, names = [], [], []
            for i, group in enumerate(groups[batch_start:batch_start+24]):
                cue = directory / f'cue-{batch_start+i}.mp3'
                concat_mp3_files([results[index] for index in group['indices']], cue,
                                 cancel_event=cancel_event)
                probe = run(['-i', str(cue), '-f', 'null', '-'])
                match = re.search(rb'Duration:\s*(\d+):(\d+):([\d.]+)', probe.stderr)
                if not match:
                    raise EverAIError('Không đo được độ dài cue SRT.')
                duration = int(match[1])*3600 + int(match[2])*60 + float(match[3])
                ratio = max(1.0, duration/(group['end']-group['start']))
                tempo = []
                while ratio > 2:
                    tempo.append('atempo=2')
                    ratio /= 2
                tempo.append(f'atempo={ratio:.8f}')
                delay = round(group['start']*44100)
                filters.append(f'[{i}:a]' + ','.join(tempo) +
                               f',aresample=44100,adelay={delay}S:all=1[a{i}]')
                names.append(f'[a{i}]')
                args += ['-i', str(cue)]
            stem = directory / f'stem-{batch_start}.wav'
            filters.append(''.join(names)+f'amix=inputs={len(names)}:normalize=0[out]')
            run([*args, '-filter_complex', ';'.join(filters), '-map', '[out]',
                 '-ar', '44100', '-ac', '1', str(stem)])
            stems.append(stem)
        # Merge the padded stems progressively, limiting the input count here too.
        merged = stems[0]
        for i, stem in enumerate(stems[1:]):
            next_mix = directory / f'mix-{i}.wav'
            run(['-i', str(merged), '-i', str(stem), '-filter_complex',
                 'amix=inputs=2:normalize=0', '-ar', '44100', '-ac', '1', str(next_mix)])
            merged = next_mix
        total = max(group['end'] for group in groups)
        run(['-i', str(merged), '-af', f'apad,atrim=duration={total:.3f}',
             '-ar', '44100', '-ac', '1', '-b:a', '128k', str(target)])
    return Path(target)
