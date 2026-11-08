"""Offline release diagnostic: synthetic audio and an isolated, signed test wallet.

Never opens production application data, imports a production grant or calls TTS.
The temporary issuer and its public keys exist only inside this diagnostic.
"""
import base64
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import audio
from .runner import SrtRunner
from .store import SrtProject
try:
    from ..engine import EverAITTSEngine
    from ..license_manager import LicenseManager, PRODUCT, canonical
except ImportError:
    from engine import EverAITTSEngine
    from license_manager import LicenseManager, PRODUCT, canonical


def run_smoke():
    with tempfile.TemporaryDirectory(prefix='everai-srt-smoke-') as temporary:
        root = Path(temporary)
        private = Ed25519PrivateKey.generate()
        key_id = 'f' * 32
        keys = {key_id: base64.b64encode(private.public_key().public_bytes_raw()).decode()}
        machine = 'EV1-' + 'B' * 64
        manager = LicenseManager(root / 'test-wallet', keys=keys, machine=machine)
        payload = dict(schema_version=1, product=PRODUCT, key_id=key_id, license_id=str(uuid.uuid4()),
                       machine_id=machine, char_allowance=100000, issued_at=int(time.time()))
        manager.import_license(canonical(dict(payload=payload,
            signature=base64.b64encode(private.sign(canonical(payload))).decode())))
        pcm, mp3 = root / 'source.wav', root / 'source.mp3'
        with audio.open_writer(pcm) as writer:
            writer.writeframes(b'\x10\x00' * 24000)
        audio.run_ffmpeg(['-i', pcm, '-c:a', 'libmp3lame', mp3], threading.Event())
        sample = mp3.read_bytes()
        source = root / 'synthetic.srt'
        audio.atomic_bytes(source, b'1\n00:00:00,100 --> 00:00:01,100\nHello.\n\n2\n00:00:02,000 --> 00:00:03,000\nWorld.')
        project = SrtProject.create(root / 'projects', source)
        config = dict(speaker_id='vi_male_leduc_mb', model_id='everai-v1.6', speed=1., pitch=1.,
                      volume=100, bitrate='128 kbps', audio_type='mp3')
        calls = []
        def factory(config, concurrency):
            engine = EverAITTSEngine(license_manager=manager, temp_dir=root / 'chunks', concurrency=concurrency)
            def render(index, text, total, target, *args):
                calls.append(text)
                audio.atomic_bytes(target, sample)
                return Path(target)
            engine._process_single_chunk = render
            return engine
        wav = SrtRunner(project, factory, manager).process(root / 'out.wav', config)
        assert audio.wave_frames(wav) == 144000
        assert len(calls) == 2 and manager.balance()['available'] == 100000 - len('Hello.World.')
        before = manager.balance()['available']
        def no_render(*args):
            raise AssertionError('Fit/export called TTS')
        mp3 = SrtRunner(project, no_render, None).process(root / 'out.mp3', config, output_format='mp3')
        assert mp3.is_file() and manager.balance()['available'] == before
        assert manager.balance()['held'] == 0
    result = dict(srt_offline_smoke=True, cues=2, frames=144000, rate=48000,
                  wav=True, mp3=True, reexport_without_tts=True, temporary_signed_wallet=True)
    print(json.dumps(result))
    return result
