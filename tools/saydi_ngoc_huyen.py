"""Standalone Saydi smoke tool. Run from root with python -m tools.saydi_ngoc_huyen."""

import argparse
import os
import secrets
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from src.everai.utils import (
    concat_mp3_files,
    ensure_ffmpeg,
    publish_audio,
    run_audio_command,
    split_text_into_chunks,
)


ORIGIN = 'https://voice.saydi.ai'
VOICE = 'vi-hn-ngoc-huyen'


def _post(session, stage, url, **kwargs):
    try:
        response = session.post(url, timeout=25, allow_redirects=False, **kwargs)
    except requests.RequestException as error:
        raise RuntimeError(f'{stage}: {type(error).__name__}') from None
    if response.status_code != 200:
        retry_after = response.headers.get('Retry-After', '')
        delay = ''
        if retry_after.isascii() and retry_after.isdecimal() and len(retry_after) < 10:
            delay = f', Retry-After {int(retry_after)} giây'
        raise RuntimeError(f'{stage}: HTTP {response.status_code}{delay}')
    return response


def _token(response, field, stage):
    try:
        value = response.json()[field]
        if isinstance(value, str) and value:
            return value
    except (ValueError, KeyError, TypeError):
        pass
    raise RuntimeError(f'{stage}: phản hồi thiếu token')


def create_access_token(session, firebase_key):
    firebase = _post(session, 'Firebase sign-up',
        'https://identitytoolkit.googleapis.com/v1/accounts:signUp',
        params={'key': firebase_key}, json={
            'email': f'user_{secrets.token_hex(12)}@mail.com',
            'password': f'{secrets.token_urlsafe(24)}!Aa1',
            'returnSecureToken': True,
        })
    identity = _token(firebase, 'idToken', 'Firebase')
    gateway = _post(session, 'Saydi auth', f'{ORIGIN}/api/auth/google',
        headers={'Referer': f'{ORIGIN}/vi/'}, json={'id_token': identity})
    return _token(gateway, 'access_token', 'Saydi auth')


def synthesize(text, destination, *, access_token=None, firebase_key=None):
    """Render with one Saydi account and publish a validated MP3."""
    chunks = split_text_into_chunks(text, max_length=320)
    if not chunks:
        raise ValueError('Văn bản trống.')
    if not access_token and not firebase_key:
        raise ValueError('Cần SAYDI_ACCESS_TOKEN hoặc SAYDI_FIREBASE_API_KEY.')
    ffmpeg = ensure_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('Thiếu FFmpeg.')
    destination = Path(destination).resolve()
    if destination.suffix.lower() != '.mp3':
        raise ValueError('Đường dẫn đầu ra phải có đuôi .mp3.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='saydi-', dir=destination.parent) as work, requests.Session() as session:
        session.headers.update({'X-OmniVoice-Client': 'web', 'Origin': ORIGIN})
        token = access_token or create_access_token(session, firebase_key)
        headers = {
            'Authorization': f'Bearer {token}',
            'Referer': f'{ORIGIN}/vi/voices/hn-ngoc-huyen/',
            'X-OV-Screen': '1920x1080',
        }
        paths = []
        for index, chunk in enumerate(chunks, start=1):
            response = _post(session, f'Saydi TTS đoạn {index}', f'{ORIGIN}/api/tts/demo',
                headers=headers, json={'text': chunk, 'sample': VOICE, 'output_format': 'mp3'})
            if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'audio/mpeg' or not response.content:
                raise RuntimeError(f'Saydi TTS đoạn {index}: phản hồi không phải MP3.')
            path = Path(work) / f'{index:04d}.mp3'
            path.write_bytes(response.content)
            paths.append(path)
        combined = concat_mp3_files(paths, Path(work) / 'combined.mp3')
        run_audio_command([ffmpeg, '-v', 'error', '-xerror', '-i', str(combined),
                           '-map', '0:a:0', '-f', 'null', '-'])
        return publish_audio(combined, destination)


def main():
    parser = argparse.ArgumentParser(description='Tạo MP3 giọng Saydi Ngọc Huyền.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--text-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        text = args.text if args.text_file is None else args.text_file.read_text(encoding='utf-8-sig')
        result = synthesize(text, args.output, access_token=os.getenv('SAYDI_ACCESS_TOKEN'),
                            firebase_key=os.getenv('SAYDI_FIREBASE_API_KEY'))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
    print(result)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    main()
