"""CapCut task protocol regression, entirely offline with temporary audio/data."""
import base64
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.everai.client import EverAIError, EverAICancelled
from src.everai.tts import capcut, service
from src.everai.tts.engine import MultiProviderTTSEngine


class FastEvent:
    def __init__(self):
        self.cancelled = False
        self.elapsed = 0

    def is_set(self):
        return self.cancelled

    def wait(self, seconds):
        self.elapsed += seconds
        return self.cancelled


class Response:
    def __init__(self, data=None, content=b'ID3' + b'x' * 200):
        self.data, self.content = data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def task_response(status='succeed', audio=None):
    payload = json.dumps(dict(audio_subtitles=audio if audio is not None else
                             [dict(speech_url='https://audio.example/test.mp3')]))
    return dict(ret=0, data=dict(tasks=[dict(status=status, payload=payload)]))


class Session:
    def __init__(self, polls=None):
        self.proxies = {'https': 'http://must-not-be-used.invalid'}
        self.trust_env = True
        self.calls = []
        self.polls = iter(polls or [task_response()])
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith(capcut.NEW_PATH):
            return Response(dict(ret=0, data=dict(tasks=[dict(id='task', token='test-token')])))
        value = next(self.polls)
        if isinstance(value, Exception):
            raise value
        return Response(value)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response()

    def close(self):
        self.closed = True


def client(session, event=None, timeout=90):
    event = event or FastEvent()
    return capcut.CapCutClient(event, session, clock=lambda: event.elapsed, poll_timeout=timeout)


def test_signature_encrypts_reference_message(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(capcut, 'PUBLIC_KEY', private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    ssml, extra = '<speak>Xin CHÀO</speak>', '{"benefit_info":{}}'
    encoded = capcut.payload_signature(ssml, extra, {'device_id': '123'})
    clear = private.decrypt(base64.b64decode(encoded), padding.PKCS1v15()).decode()
    assert clear == ('appid:359289&did:123&creditDisable:false&ssml:' +
                     hashlib.md5(ssml.encode()).hexdigest() + '&extraInfo:' + extra)


def test_reference_ssml_resource_and_text_preserved(monkeypatch):
    monkeypatch.setattr(capcut, 'payload_signature', lambda *args: 'SIGN')
    body = capcut.task_body('Xin CHÀO & <test> "x"', 'BV075_streaming', '7102355803792740865', {})
    assert body['can_queue'] is True
    task = body['tasks'][0]
    assert (task['req_key'], task['task_version']) == ('sami_text_to_speech', 'v3')
    payload = json.loads(task['payload'])
    assert payload['credit_disable'] is False
    assert payload['extra_info'] == '{"benefit_info":{}}'
    assert 'resource_id="7102355803792740865"' in payload['ssml']
    assert '<prosody rate="1.0">Xin CHÀO &amp; &lt;test&gt; &quot;x&quot;</prosody>' in payload['ssml']


def test_direct_create_poll_download_and_header_signatures(tmp_path, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://broken.invalid:1')
    session = Session([task_response('processing'), task_response()])
    target = tmp_path / 'chunk.mp3'
    assert client(session).synthesize('Xin CHÀO', 'BV075_streaming', target) == target
    assert target.read_bytes().startswith(b'ID3')
    assert session.trust_env is False and session.proxies == {} and session.closed
    for url, kwargs in session.calls[:-1]:
        headers = kwargs['headers']
        path = capcut.NEW_PATH if url.endswith(capcut.NEW_PATH) else capcut.QUERY_PATH
        expected = f'9e2c|{path[-7:]}|3|8.7.0|{headers["device-time"]}|{headers["tdid"]}|11ac'
        assert headers['sign'] == hashlib.md5(expected.encode()).hexdigest()
        assert headers['x-ss-stub'] == hashlib.md5(kwargs['data']).hexdigest()
        assert headers['appid'] == '359289'
    assert 'region' in session.calls[0][1]['params']
    assert 'region' not in session.calls[1][1]['params']
    query = json.loads(session.calls[1][1]['data'])['tasks'][0]
    assert query == dict(bind_id='', id='task', req_key='sami_text_to_speech', task_version='v3', token='test-token')


@pytest.mark.parametrize('audio', [[], [{}, {}], [dict(invalid_input=True, speech_url='x')], [{}]])
def test_bad_audio_mapping_never_publishes(tmp_path, audio):
    session = Session([task_response(audio=audio)])
    with pytest.raises(EverAIError):
        client(session).synthesize('Xin chào', 'BV075_streaming', tmp_path / 'bad.mp3')
    assert not (tmp_path / 'bad.mp3').exists()
    assert session.closed


def test_terminal_failure_stops_polling(tmp_path):
    session = Session([task_response('failed')])
    with pytest.raises(EverAIError, match='không hoàn thành'):
        client(session).synthesize('Xin chào', 'BV075_streaming', tmp_path / 'bad.mp3')
    assert len(session.calls) == 2


def test_poll_transient_network_then_success(tmp_path):
    session = Session([requests.Timeout(), task_response()])
    client(session).synthesize('Xin chào', 'BV075_streaming', tmp_path / 'ok.mp3')
    assert len(session.calls) == 4


def test_poll_deadline_and_cancel(tmp_path):
    event = FastEvent()
    session = Session([task_response('processing')] * 10)
    with pytest.raises(EverAIError, match='Hết thời gian'):
        client(session, event, timeout=2).synthesize('Xin chào', 'BV075_streaming', tmp_path / 'bad.mp3')
    event.cancelled = True
    with pytest.raises(EverAICancelled):
        client(Session(), event).synthesize('Xin chào', 'BV075_streaming', tmp_path / 'bad.mp3')


def test_capcut_service_ignores_proxy_and_applies_speed_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(service, 'ensure_ffmpeg', lambda: 'ffmpeg')
    def render(self, text, voice, target):
        Path(target).write_bytes(b'ID3' + b'x' * 200)
    monkeypatch.setattr(service.CapCutClient, 'synthesize', render)
    def normalize(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b'ID3' + b'x' * 200)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service, 'run_audio_command', normalize)
    adapter = service.TTSService()
    adapter.node = None
    adapter.synthesize('Xin CHÀO', tmp_path / 'ok.mp3', 'capcut|BV075_streaming',
                       speed=1.5, proxy='socks5://bad.invalid:1')
    assert calls[0].count('-af') == 1 and 'atempo=1.5' in calls[0]


def test_capcut_engine_does_not_consult_proxy_catalog(tmp_path):
    engine = MultiProviderTTSEngine.__new__(MultiProviderTTSEngine)
    engine.cancel_event = threading.Event()
    engine.license_manager = None
    engine.get_current_proxy = lambda: pytest.fail('CapCut must bypass proxy selection')
    engine._status = lambda *a, **k: None
    engine.log = lambda *a: None
    captured = []
    engine.service = SimpleNamespace(synthesize=lambda *args: captured.append(args) or tmp_path / 'ok.mp3')
    engine._process_single_chunk(1, 'Xin chào', 1, tmp_path / 'ok.mp3',
                                 'capcut|BV075_streaming', 'everai-v1.6', 1, 1, 100, '128 kbps', 'mp3', True)
    assert captured[0][4] is None
