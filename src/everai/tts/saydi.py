"""Saydi rendering with a shared, renewable in-memory session."""

import json
import re
import secrets
import threading
import time
import requests

try:
    from ..client import EverAIError
except ImportError:
    from client import EverAIError

VOICE_ID = 'vi-hn-ngoc-huyen'
CHUNK_LIMIT = 320
ORIGIN = 'https://voice.saydi.ai'
STUDIO = f'{ORIGIN}/vi/studio/tts/'
HEADERS = {'Origin': ORIGIN, 'Referer': STUDIO, 'X-OmniVoice-Client': 'web'}


def _auth_request(method, url, stage, **options):
    try:
        response = requests.request(method, url, timeout=(5, 20), allow_redirects=False, **options)
    except requests.RequestException as error:
        raise EverAIError(f'{stage}: lỗi kết nối ({type(error).__name__}).') from None
    if response.status_code != 200:
        raise EverAIError(f'{stage}: HTTP {response.status_code}.')
    return response


def create_session():
    page = _auth_request('GET', STUDIO, 'Cấu hình Saydi')
    match = re.search(r'window\.OV_FIREBASE\s*=\s*(\{[^\r\n]+?\})\s*;', page.text)
    try:
        key = json.loads(match[1])['apiKey'] if match else ''
        if not isinstance(key, str) or not re.fullmatch(r'AIza[\w-]+', key):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise EverAIError('Không đọc được cấu hình đăng nhập công khai của Saydi.') from None
    firebase = _auth_request('POST', 'https://identitytoolkit.googleapis.com/v1/accounts:signUp',
                             'Tạo phiên Firebase', params={'key': key}, json={
        'email': f'user_{secrets.token_hex(12)}@mail.com',
        'password': f'{secrets.token_urlsafe(24)}!Aa1',
        'returnSecureToken': True,
    })
    try:
        identity = firebase.json()['idToken']
        if not isinstance(identity, str) or not identity:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise EverAIError('Firebase không trả ID token.') from None
    gateway = _auth_request('POST', f'{ORIGIN}/api/auth/google', 'Xác thực Saydi',
                             headers=HEADERS, json={'id_token': identity})
    try:
        result = gateway.json()
        token = result['access_token']
        ttl = int(result.get('expires_in', 1800))
        if not isinstance(token, str) or not token or ttl <= 0:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise EverAIError('Saydi không trả phiên hợp lệ.') from None
    return token, time.monotonic() + max(1, ttl - 30)


class SaydiSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.expires = 0

    def get(self, rejected=None):
        with self.lock:
            if self.token is None or self.token == rejected or time.monotonic() >= self.expires:
                self.token, self.expires = create_session()
            return self.token


AUTO_SESSION = SaydiSession()


class SaydiClient:
    def __init__(self, token):
        self.token = token

    def synthesize(self, text, voice, target):
        if len(text) > CHUNK_LIMIT:
            raise ValueError('Đoạn Saydi vượt quá 320 ký tự.')
        token = self.token or AUTO_SESSION.get()
        response = self.request(text, voice, token)
        if not self.token and self.should_renew(response):
            response = self.request(text, voice, AUTO_SESSION.get(rejected=token))
        if response.status_code in (401, 403):
            raise EverAIError('Phiên Saydi không hợp lệ hoặc đã hết hạn.')
        if response.status_code == 429:
            raise EverAIError('Saydi đang giới hạn yêu cầu hoặc đã hết hạn mức.')
        if response.status_code != 200:
            raise EverAIError(f'Saydi trả về HTTP {response.status_code}.')
        if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'audio/mpeg':
            raise EverAIError('Saydi không trả về MP3.')
        target.write_bytes(response.content)

    @staticmethod
    def should_renew(response):
        return (response.status_code == 401 or
                (response.status_code == 429 and 'plan-quota-exceeded' in response.text.lower()))

    @staticmethod
    def request(text, voice, token):
        try:
            return requests.post(
                f'{ORIGIN}/api/tts/demo',
                headers={**HEADERS, 'Authorization': f'Bearer {token}', 'X-OV-Screen': '1920x1080'},
                json={'text': text, 'sample': voice, 'output_format': 'mp3'},
                timeout=(5, 25), allow_redirects=False)
        except requests.RequestException as error:
            raise EverAIError(f'Lỗi kết nối Saydi ({type(error).__name__}).') from None
