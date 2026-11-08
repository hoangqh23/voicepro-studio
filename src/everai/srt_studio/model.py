"""Pure SRT data contracts. Timestamps remain integer milliseconds."""
import hashlib
import json
import math
import re
import uuid

try:
    from ..utils import normalize_tts_text, split_text_into_chunks
    from ..tts.saydi import CHUNK_LIMIT
except ImportError:
    from utils import normalize_tts_text, split_text_into_chunks
    from tts.saydi import CHUNK_LIMIT

VERSION = 1
MAX_BYTES = 10 * 1024 * 1024
MAX_CUES = 10000
MAX_MS = 6 * 3600 * 1000
CONFIG_KEYS = ('speaker_id', 'model_id', 'speed', 'pitch', 'volume', 'bitrate', 'audio_type')
ID = re.compile(r'^[0-9a-f]{32}$')
TIMESTAMP = r'(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})'
TIMING = re.compile(r'^' + TIMESTAMP + r'\s*-->\s*' + TIMESTAMP + r'(?:\s+.*)?$')


def identifier():
    return uuid.uuid4().hex


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def speech_text(text):
    return normalize_tts_text(re.sub(r'</?(?:i|b|u|s|font|ruby|rt)(?:\s[^>]*)?>', '',
                                    text, flags=re.IGNORECASE))


def chunks_for(text, provider):
    return split_text_into_chunks(speech_text(text), max_length=CHUNK_LIMIT if provider == 'saydi' else 450)


def canonical_config(config):
    if not isinstance(config, dict) or any(k not in config for k in CONFIG_KEYS):
        raise ValueError('Thiếu cấu hình giọng của project SRT.')
    result = {k: config[k] for k in CONFIG_KEYS}
    if not all(isinstance(result[k], str) and result[k] for k in ('speaker_id', 'model_id', 'bitrate')):
        raise ValueError('Cấu hình giọng SRT không hợp lệ.')
    for key in ('speed', 'pitch'):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not .5 <= value <= 2:
            raise ValueError('Tốc độ/cao độ phải trong khoảng 0,5–2,0.')
    if result['volume'] != 100 or result['audio_type'] != 'mp3':
        raise ValueError('Audio nguồn SRT phải dùng cấu hình MP3 của Studio.')
    return result


def format_ms(value):
    seconds, ms = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'


def timestamp_ms(value):
    match = re.fullmatch(TIMESTAMP, value.strip())
    if not match:
        raise ValueError('Mốc thời gian cần dạng HH:MM:SS,mmm.')
    h, m, s, ms = map(int, match.groups())
    if m >= 60 or s >= 60:
        raise ValueError('Phút/giây phải nhỏ hơn 60.')
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def validate_cues(cues):
    if not isinstance(cues, list) or not 1 <= len(cues) <= MAX_CUES:
        raise ValueError(f'SRT cần có 1–{MAX_CUES:,} cue.')
    ids = set()
    text_bytes = 0
    for cue in cues:
        if not isinstance(cue, dict) or not ID.fullmatch(str(cue.get('id', ''))) or cue['id'] in ids:
            raise ValueError('ID cue SRT không hợp lệ hoặc trùng.')
        ids.add(cue['id'])
        if (not isinstance(cue.get('number'), str) or not cue['number'] or
                type(cue.get('source_order')) is not int or cue['source_order'] < 0 or
                not isinstance(cue.get('state'), str)):
            raise ValueError('Metadata cue SRT không hợp lệ.')
        start, end = cue.get('start_ms'), cue.get('end_ms')
        if (type(start) is not int or type(end) is not int or
                not 0 <= start < end <= MAX_MS):
            raise ValueError(f'Cue {cue.get("number", "?")}: thời gian không hợp lệ (tối đa 6 giờ).')
        text = cue.get('text')
        if not isinstance(text, str) or not any(c.isalnum() for c in speech_text(text)):
            raise ValueError(f'Cue {cue.get("number", "?")}: không có lời đọc; hãy sửa hoặc bỏ cue.')
        text_bytes += len(text.encode('utf-8'))
        if text_bytes > MAX_BYTES:
            raise ValueError('Tổng lời thoại SRT vượt giới hạn 10 MiB.')
    previous = None
    for cue in sorted(cues, key=lambda c: (c['start_ms'], c['source_order'])):
        if previous is not None and cue['start_ms'] < previous['end_ms']:
            raise ValueError(f'Cue {cue["number"]} chồng thời gian với cue {previous["number"]}. Hãy sửa trước khi tạo giọng.')
        if previous is None or cue['end_ms'] > previous['end_ms']:
            previous = cue


def parse_srt(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('SRT vượt giới hạn 10 MiB.')
    lines = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    cues, warnings, seen_numbers = [], [], set()
    position = 0
    while position < len(lines):
        if not lines[position].strip():
            position += 1
            continue
        start_line = position + 1
        number = str(len(cues) + 1)
        if lines[position].strip().isdigit():
            number = lines[position].strip()
            position += 1
        match = TIMING.fullmatch(lines[position].strip()) if position < len(lines) else None
        if not match:
            raise ValueError(f'Cue {number}, dòng {position + 1}: timestamp SRT không hợp lệ.')
        groups = match.groups()
        try:
            start = timestamp_ms(':'.join(groups[:3]) + ',' + groups[3])
            end = timestamp_ms(':'.join(groups[4:7]) + ',' + groups[7])
        except ValueError as error:
            raise ValueError(f'Cue {number}, dòng {position + 1}: {error}') from error
        position += 1
        body = []
        while position < len(lines) and lines[position].strip():
            if TIMING.fullmatch(lines[position].strip()):
                raise ValueError(f'Dòng {position + 1}: thiếu dòng trống ngăn cách cue SRT.')
            body.append(lines[position])
            position += 1
        if number in seen_numbers or number != str(len(cues) + 1):
            warnings.append(f'Số cue {number} không liên tiếp hoặc trùng (dòng {start_line}).')
        seen_numbers.add(number)
        cues.append(dict(id=identifier(), number=number, source_order=len(cues), source_line=start_line,
                         start_ms=start, end_ms=end, text='\n'.join(body), state='pending', generation=None))
        if len(cues) > MAX_CUES:
            raise ValueError(f'SRT vượt giới hạn {MAX_CUES:,} cue.')
    validate_cues(cues)
    if any(a['start_ms'] > b['start_ms'] for a, b in zip(cues, cues[1:])):
        warnings.append('Cue được sắp theo thời gian; timestamp gốc được giữ nguyên.')
    return cues, warnings
