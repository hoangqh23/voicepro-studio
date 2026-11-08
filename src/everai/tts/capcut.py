"""Direct CapCut common-task protocol, matched to VIRAL_REUP's TTS contract.

Only public protocol/catalog data is carried over; no reference-app runtime,
account, cookie, device file or licensing code is loaded.
"""
import base64
import hashlib
import json
from pathlib import Path
import random
import time
import uuid
from xml.sax.saxutils import escape

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

try:
    from ..client import EverAIError, EverAICancelled
except ImportError:
    from client import EverAIError, EverAICancelled

BASE_URL = 'https://editor-api-sg.capcutapi.com'
NEW_PATH = '/lv/v1/common_task/new'
QUERY_PATH = '/lv/v1/common_task/query'
APP_VERSION = '8.7.0'
APP_ID = '359289'
BABI = dict(feature_entrance='editor', feature_entrance_detail='editor-feature-text_to_speech',
            feature_key='text_to_speech', scenario='video_editor')
CATALOG = json.loads((Path(__file__).parent / 'providers/capcut_voices.json').read_text('utf-8'))
PUBLIC_KEY = b'''-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmTd34Lw4b7IuldSXh/zY
CMla+ITdGG5TeWz6ad+OySd4r+IrY45AoqrYUxhQ2dl+7z+i7r/5vEa8rr39BYfB
8AGMQLmZA8HmgpWBsqrn/V6daUALkKnkLb70Fn32CJigIuGXAYqxUdGuI340aC+0
v5Es3puJsHyzf01/AelE4Cdc6bZhQrASJLBh8R3BQToYClmDVSDUQk28o8sl/guA
Z4n303Vj+6Siv1HayPCdV6kpVVnMBAG4+umUbwGmn132N3fgpzLarFF3XyWmS1zh
D/J07iM/rP8GDO9IskHNHd2phrO0G6KzrcFAnTBHjVv+hCBEfzN/no3FNA9AuC36
mwIDAQAB
-----END PUBLIC KEY-----'''


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def catalog_voices():
    return [dict(engine='capcut', voiceId=voice, label=label, lang=lang,
                 resourceId=resource, region='')
            for lang, entries in CATALOG.items() for voice, label, resource in entries]


def device_profile():
    device = str(random.SystemRandom().randint(7000000000000000000, 8000000000000000000))
    platform, kind, version = random.SystemRandom().choice([
        ('mac', 'MacBookPro17,4', '15.7.4'), ('mac', 'MacBookPro18,3', '14.6.1'),
        ('windows', 'Windows', '10.0.19045')])
    return dict(aid=APP_ID, app_name='CapCut', appvr=APP_VERSION, version_name=APP_VERSION,
                version_code=APP_VERSION, channel='capcutpc_google', device_platform=platform,
                device_type=kind, device_brand=kind, os_version=version, device_id=device,
                iid=device, tdid=device, region='VN', loc='VN', lan='vi-VN', pf='3')


def payload_signature(ssml, extra, device):
    message = (f'appid:{APP_ID}&did:{device["device_id"]}&creditDisable:false&ssml:'
               f'{hashlib.md5(ssml.encode("utf-8")).hexdigest()}&extraInfo:{extra}')
    key = serialization.load_pem_public_key(PUBLIC_KEY)
    return base64.b64encode(key.encrypt(message.encode('utf-8'), padding.PKCS1v15())).decode('ascii')


def task_body(text, voice, resource, device):
    escaped = escape(text, {'"': '&quot;', "'": '&apos;'})
    ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">\n'
            f'    <voice name="{escape(voice)}" mock_tone_info="" platform="sami" '
            f'resource_id="{escape(resource)}" emotion="" emotion_scale="0" style="" role="" '
            'moyin_emotion="" is_clone_tone="false" need_subtitle_timestamp="false">\n'
            f'        <prosody rate="1.0">{escaped}</prosody>\n    </voice>\n</speak>')
    extra = compact(dict(benefit_info={}))
    payload = dict(audio_format='mp3', babi_param=compact(BABI), credit_disable=False,
                   extra_info=extra, need_merge_voice=False, need_subtitle_timestamp=False,
                   scene='text_to_speech', ssml=ssml, sign=payload_signature(ssml, extra, device))
    return dict(bind_id=str(uuid.uuid4()), can_queue=True, enter_from='text_to_speech',
                tasks=[dict(context=str(uuid.uuid4()), payload=compact(payload),
                            req_key='sami_text_to_speech', task_version='v3')])


class CapCutClient:
    def __init__(self, cancel_event, session=None, clock=time.monotonic, poll_timeout=90):
        self.cancel_event = cancel_event
        self.session = session if session is not None else requests.Session()
        # Ignore environment proxies, OS proxy discovery and caller proxy catalogs.
        self.session.trust_env = False
        self.session.proxies.clear()
        self.clock = clock
        self.poll_timeout = poll_timeout

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise EverAICancelled('Đã dừng tác vụ.')

    def _post(self, path, body, device, create=False, timeout=60):
        self._check_cancel()
        raw = compact(body)
        now = str(int(time.time()))
        seed = uuid.uuid4().hex
        headers = {'content-type': 'application/json', 'appvr': APP_VERSION,
                   'ch': device['channel'], 'device-time': now, 'lan': device['lan'],
                   'loc': device['loc'], 'pf': '3', 'sign-ver': '1', 'tdid': device['tdid'],
                   'x-ss-stub': hashlib.md5(raw.encode('utf-8')).hexdigest(),
                   'x-ss-dp': APP_ID, 'x-khronos': now,
                   'x-tt-trace-id': f'00-{seed}-{seed[:16]}-01',
                   'user-agent': 'Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03',
                   'accept-encoding': 'gzip, deflate', 'store-country-code': 'vn',
                   'store-country-code-src': 'did', 'is-dispatch-us-ttp': '0',
                   'is-app-region-us-ttp': '0'}
        headers.update({'app-sdk-version': APP_VERSION, 'appid': APP_ID})
        signing = f'9e2c|{path[-7:]}|3|{APP_VERSION}|{now}|{device["tdid"]}|11ac'
        headers['sign'] = hashlib.md5(signing.encode()).hexdigest()
        fields = ('app_name', 'device_type', 'os_version', 'channel', 'version_name',
                  'device_brand', 'device_id', 'iid', 'version_code', 'device_platform', 'aid')
        query = {key: device[key] for key in fields}
        if create:
            query.update(region=device['region'], babi_param=compact(BABI))
        response = self.session.post(BASE_URL + path, params=query, headers=headers,
                                     data=raw.encode('utf-8'), timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise EverAIError('CapCut trả về dữ liệu task không hợp lệ.')
        if str(data.get('ret')) != '0':
            # Never echo complete responses: task tokens and URLs can be sensitive.
            raise EverAIError('CapCut từ chối yêu cầu tạo/đọc task.')
        return data

    def synthesize(self, text, voice, output):
        self._check_cancel()
        selected = next((v for v in catalog_voices() if v['voiceId'] == voice), None)
        if selected is None:
            raise EverAIError('Giọng CapCut cũ không thuộc catalog task API. Hãy chọn lại giọng CapCut.')
        if not text or not text.strip():
            raise EverAIError('Văn bản CapCut không được để trống.')
        device = device_profile()
        try:
            created = self._post(NEW_PATH, task_body(text, voice, selected['resourceId'], device), device, True)
            task = created['data']['tasks'][0]
            query = dict(tasks=[dict(bind_id='', id=task['id'], req_key='sami_text_to_speech',
                                     task_version='v3', token=task['token'])])
            deadline = self.clock() + self.poll_timeout
            while self.clock() < deadline:
                if self.cancel_event.wait(min(1, max(0, deadline - self.clock()))):
                    self._check_cancel()
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                try:
                    polled = self._post(QUERY_PATH, query, device, timeout=min(60, remaining))
                except (requests.RequestException, ValueError):
                    continue
                item = polled['data']['tasks'][0]
                status = item.get('status')
                if status == 'succeed':
                    payload = json.loads(item['payload'])
                    audio = payload['audio_subtitles']
                    if len(audio) != 1 or audio[0].get('invalid_input') or not audio[0].get('speech_url'):
                        raise EverAIError('CapCut trả về audio không khớp đoạn văn bản.')
                    self._check_cancel()
                    response = self.session.get(audio[0]['speech_url'], timeout=60)
                    response.raise_for_status()
                    content = response.content
                    if len(content) < 128 or not (content.startswith(b'ID3') or
                            (content[0] == 255 and content[1] & 224 == 224)):
                        raise EverAIError('CapCut không trả về MP3 hợp lệ.')
                    self._check_cancel()
                    Path(output).write_bytes(content)
                    return Path(output)
                if status not in ('queueing', 'processing', 'running'):
                    raise EverAIError('Task CapCut không hoàn thành được.')
            raise EverAIError('Hết thời gian chờ task CapCut.')
        except requests.RequestException:
            raise EverAIError('Lỗi kết nối trực tiếp tới CapCut.') from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise EverAIError('Phản hồi task CapCut không hợp lệ.') from None
        finally:
            self.session.close()
