"""Per-cue durable jobs use real temporary wallets and offline TTS renderers."""
import copy
import json
from pathlib import Path
import threading
import time

import pytest

from src.everai.engine import EverAITTSEngine
from src.everai.client import EverAICancelled, EverAIError
from src.everai.license_manager import LicenseError
from src.everai.srt_studio import SrtProject, SrtRunner
from src.everai.srt_studio import audio, model
from src.everai.srt_studio.store import project_lock
from src.everai.tts.engine import MultiProviderTTSEngine
from src.everai.utils import atomic_json, read_json

CONFIG = dict(speaker_id='vi_male_leduc_mb', model_id='everai-v1.6', speed=1., pitch=1.,
              volume=100, bitrate='128 kbps', audio_type='mp3')


def project(tmp_path, texts=('Xin chào.', 'Hẹn gặp lại.')):
    source = tmp_path / 'input.srt'
    source.write_text('\n\n'.join(f'{i+1}\n00:00:{i*4:02},100 --> 00:00:{i*4+3:02},100\n{text}'
                                for i, text in enumerate(texts)), encoding='utf-8-sig')
    return SrtProject.create(tmp_path / 'srt-projects', source)


@pytest.fixture
def offline(tmp_path, funded_license, monkeypatch):
    pcm, mp3 = tmp_path / 'source.wav', tmp_path / 'source.mp3'
    with audio.open_writer(pcm) as writer:
        writer.writeframes(b'\x10\x00' * 38400)
    audio.run_ffmpeg(['-i', pcm, '-c:a', 'libmp3lame', mp3], threading.Event())
    sample = mp3.read_bytes()
    calls = []
    def render(self, idx, text, total, path, *args):
        calls.append((idx, text, total, self.concurrency))
        Path(path).write_bytes(sample)
        return Path(path)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    monkeypatch.setattr(MultiProviderTTSEngine, '_process_single_chunk', render)
    def factory(config, concurrency):
        provider = config['speaker_id'].partition('|')[0]
        cls = EverAITTSEngine if provider not in ('capcut', 'saydi', 'edge', 'elevenlabs') else MultiProviderTTSEngine
        kwargs = dict(input_mode='text') if cls is MultiProviderTTSEngine else {}
        return cls(**kwargs, license_manager=funded_license, concurrency=concurrency,
                   temp_dir=tmp_path / 'temp')
    return factory, calls, funded_license


def test_parser_preserves_ms_markup_and_diagnostics():
    cues, warnings = model.parse_srt('\ufeff9\r\n00:00:03.001 --> 00:00:04,002\r\n<i>Xin</i>\r\nchào.\r\n\r\n9\r\n00:00:00,000 --> 00:00:01,000\r\n[cười] Xin chào.')
    assert cues[0]['start_ms'] == 3001 and cues[0]['end_ms'] == 4002
    assert model.speech_text(cues[0]['text']) == 'Xin\nchào.'
    assert '[cười]' in model.speech_text(cues[1]['text'])
    assert len(warnings) >= 2
    assert len({c['id'] for c in cues}) == 2


@pytest.mark.parametrize('text,pattern', [
    ('1\nBAD\nXin chào', 'dòng 2'),
    ('1\n00:61:00,000 --> 00:62:00,000\nXin', 'Phút/giây'),
    ('1\n00:00:02,000 --> 00:00:01,000\nXin', 'thời gian'),
    ('1\n00:00:00,000 --> 00:00:01,000\n<i></i>', 'lời đọc'),
    ('1\n00:00:00,000 --> 00:00:04,000\nA\n\n2\n00:00:01,000 --> 00:00:02,000\nB', 'chồng'),
    ('1\n00:00:00,000 --> 00:00:01,000\nA\n00:00:01,000 --> 00:00:02,000\nB', 'dòng trống'),
])
def test_parser_rejects_before_any_tts(text, pattern):
    with pytest.raises(ValueError, match=pattern):
        model.parse_srt(text)


def test_adjacent_and_ten_thousand_cues_are_valid():
    text = '\n\n'.join(f'{i+1}\n{model.format_ms(i*200)} --> {model.format_ms(i*200+200)}\nXin.'
                       for i in range(10000))
    cues, warnings = model.parse_srt(text)
    assert len(cues) == 10000 and not warnings
    assert cues[-1]['end_ms'] == 2000000


def test_project_snapshot_lock_path_and_invalid_edit(tmp_path):
    instance = project(tmp_path)
    ids = [c['id'] for c in instance.data['cues']]
    (tmp_path / 'input.srt').write_text('changed', encoding='utf-8')
    assert 'Xin chào' in (instance.root / 'source.srt').read_text('utf-8-sig')
    assert [c['id'] for c in SrtProject(instance.root).data['cues']] == ids
    with project_lock(instance.root):
        with pytest.raises(ValueError, match='cửa sổ khác'):
            with project_lock(instance.root):
                pass
    with pytest.raises(ValueError):
        instance.path('../escape.mp3')
    with pytest.raises(ValueError):
        instance.edit_cue(ids[0], 'Xin.', 0, 9000)
    instance.reload()
    assert instance.data['cues'][0]['end_ms'] == 3100


def test_render_fit_export_then_timestamp_only_is_free(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    before = manager.balance()['available']
    result = SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert result.is_file() and audio.wave_frames(result) == 7100 * 48
    assert manager.balance()['available'] == before - sum(len(t) for _, t, _, _ in calls)
    assert len(calls) == 2
    original_calls = len(calls)
    generations = [c['generation']['id'] for c in instance.data['cues']]
    assert all(not list((instance.generation_root(c) / 'job').glob('chunk_*.mp3')) for c in instance.data['cues'])
    cue = instance.data['cues'][1]
    instance.edit_cue(cue['id'], cue['text'], 5100, 8100)
    monkey_free = lambda *args: pytest.fail('Timestamp/format changes must not render TTS')
    out = SrtRunner(instance, monkey_free, None).process(tmp_path / 'again.wav', CONFIG)
    assert audio.wave_frames(out) == 8100 * 48
    assert len(calls) == original_calls
    assert [c['generation']['id'] for c in instance.data['cues']] == generations
    mp3 = SrtRunner(instance, monkey_free, None).process(tmp_path / 'again.mp3', CONFIG, output_format='mp3')
    assert mp3.suffix == '.mp3' and mp3.is_file()
    report = read_json(instance.path(instance.data['export']['report']))
    assert report['frames'] == 8100 * 48 and report['state'] == 'completed'


def test_edit_text_regenerates_only_one_cue_and_new_charge(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    SrtRunner(instance, factory, manager).process(tmp_path / 'first.wav', CONFIG)
    first = instance.data['cues'][0]
    old_generation = copy.deepcopy(first['generation'])
    instance.edit_cue(first['id'], 'Nội dung đã sửa.', first['start_ms'], first['end_ms'])
    SrtRunner(instance, factory, manager).process(tmp_path / 'second.wav', CONFIG)
    assert len(calls) == 3 and calls[-1][1] == 'Nội dung đã sửa.'
    assert first['id'] == instance.data['cues'][0]['id']
    assert old_generation['id'] != instance.data['cues'][0]['generation']['id']


def test_chunk_boundary_and_saydi_counter_match_engine():
    text = ('Xin chào các bạn. ' * 60) + ' a --> b'
    assert len(model.chunks_for(text, 'saydi')) > 1
    assert max(map(len, model.chunks_for(text, 'saydi'))) <= 320
    engine = MultiProviderTTSEngine(input_mode='text', max_chunk_chars=320)
    assert engine.split_text(model.speech_text(text)) == model.chunks_for(text, 'saydi')


def test_interrupted_long_cue_resumes_paid_chunks_without_duplicate_fee(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    instance = project(tmp_path, texts=('Xin chào các bạn. ' * 70,))
    cue = instance.data['cues'][0]
    instance.edit_cue(cue['id'], cue['text'], 100, 120000)
    original = EverAITTSEngine._process_single_chunk
    fails = [True]
    def fail_second(self, index, *args):
        if index == 2 and fails[0]:
            raise EverAIError('offline interruption')
        return original(self, index, *args)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', fail_second)
    start = manager.balance()['available']
    with pytest.raises(EverAIError):
        SrtRunner(instance, factory, manager, concurrency=1).process(tmp_path / 'out.wav', CONFIG)
    assert manager.balance()['held'] == 0
    assert calls and calls[0][0] == 1
    fails[0] = False
    result = SrtRunner(instance, factory, manager, concurrency=1).process(tmp_path / 'out.wav', CONFIG)
    assert result.is_file()
    assert sum(1 for idx, *_ in calls if idx == 1) == 1
    expected = sum(map(len, model.chunks_for(instance.data['cues'][0]['text'], 'everai')))
    assert manager.balance()['available'] == start - expected


def test_missing_raw_completed_requires_explicit_new_generation(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    cue = instance.data['cues'][0]
    raw = instance.path(cue['generation']['raw_file'])
    raw.unlink()
    with pytest.raises(ValueError, match='mất/hỏng'):
        SrtRunner(instance, factory, manager).process(tmp_path / 'second.wav', CONFIG)
    assert len(calls) == 2
    instance.regenerate(cue['id'])
    SrtRunner(instance, factory, manager).process(tmp_path / 'third.wav', CONFIG)
    assert len(calls) == 3


def test_reconcile_publish_before_parent_metadata_without_tts(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    with project_lock(instance.root):
        instance.reload()
        instance.data['cues'][0]['generation'].pop('raw')
        instance.data.update(state='interrupted', result=None)
        instance.save()
    SrtRunner(instance, lambda *a: pytest.fail('must reconcile child'), None).process(tmp_path / 'resume.wav', CONFIG)
    assert len(calls) == 2
    assert instance.data['cues'][0]['generation']['raw']


def test_overlong_cue_always_exports_and_second_export_is_free(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path, texts=('Xin chào.',))
    cue = instance.data['cues'][0]
    instance.edit_cue(cue['id'], cue['text'], 0, 100)
    result = SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert result.is_file() and instance.data['state'] == 'completed'
    assert audio.wave_frames(result) > 100 * 48
    assert instance.data['cues'][0]['fit']['overflow_frames'] > 0
    count = len(calls)
    second = SrtRunner(instance, lambda *a: pytest.fail('No second TTS render'), None).process(
        tmp_path / 'force.mp3', CONFIG, policy='force', output_format='mp3')
    assert second.is_file() and len(calls) == count


def test_forty_six_overlong_cues_shift_forward_and_always_export(tmp_path, offline):
    factory, calls, manager = offline
    source = tmp_path / 'forty-six.srt'
    source.write_text('\n\n'.join(
        f'{i + 1}\n{model.format_ms(i * 200)} --> {model.format_ms(i * 200 + 100)}\nCâu {i + 1}.'
        for i in range(46)), encoding='utf-8-sig')
    instance = SrtProject.create(tmp_path / 'srt-projects', source)

    result = SrtRunner(instance, factory, manager).process(tmp_path / 'forty-six.wav', CONFIG)

    assert result.is_file() and instance.data['state'] == 'completed'
    assert len(calls) == 46
    assert all(cue['state'] == 'ready' for cue in instance.data['cues'])
    assert all(cue['fit']['overflow_frames'] >= 0 for cue in instance.data['cues'])
    assert audio.wave_frames(result) > instance.data['cues'][-1]['end_ms'] * 48
    starts = [cue['fit']['placed_start_frame'] for cue in instance.data['cues']]
    ends = [cue['fit']['placed_end_frame'] for cue in instance.data['cues']]
    assert all(starts[i] >= ends[i - 1] for i in range(1, len(starts)))


def test_direct_provider_concurrency_has_one_worker_per_cue(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    instance = project(tmp_path, texts=('A', 'B', 'C', 'D'))
    config = dict(CONFIG, speaker_id='capcut|offline', model_id='capcut')
    active = peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    original = MultiProviderTTSEngine._process_single_chunk
    def renderer(self, *args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=5)
            return original(self, *args)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(MultiProviderTTSEngine, '_process_single_chunk', renderer)
    SrtRunner(instance, factory, manager, concurrency=2).process(tmp_path / 'out.wav', config)
    assert peak == 2 and all(concurrency == 1 for _, _, _, concurrency in calls)


def test_cancel_waits_and_releases_reservations(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    instance = project(tmp_path)
    entered = threading.Event()
    def renderer(self, *args):
        entered.set()
        self.cancel_event.wait(5)
        raise EverAICancelled('stopped')
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', renderer)
    runner = SrtRunner(instance, factory, manager)
    errors = []
    def run():
        try:
            runner.process(tmp_path / 'out.wav', CONFIG)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(3)
    runner.cancel()
    thread.join(5)
    assert not thread.is_alive() and isinstance(errors[0], EverAICancelled)
    assert manager.balance()['held'] == 0
    assert not (tmp_path / 'out.wav').exists()


def test_config_secrets_never_enter_project_json(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    config = dict(CONFIG, api_key='secret-key-test', saydi_token='secret-token-test')
    SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', config)
    assert all('secret-' not in path.read_text('utf-8') for path in instance.root.rglob('*.json'))


def test_corrupt_project_is_preserved_and_not_overwritten(tmp_path):
    instance = project(tmp_path)
    path = instance.root / 'project.json'
    path.write_bytes(b'{broken')
    with pytest.raises(ValueError):
        SrtProject(instance.root)
    assert path.read_bytes() == b'{broken'


def test_all_paid_chunks_can_assemble_after_expiry_without_credentials(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    instance = project(tmp_path, texts=('Xin chào.',))
    original = EverAITTSEngine._assemble_audio
    def fail_merge(self, *args):
        raise audio.AudioAssemblyError('offline merge interruption')
    monkeypatch.setattr(EverAITTSEngine, '_assemble_audio', fail_merge)
    with pytest.raises(audio.AudioAssemblyError):
        SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert len(calls) == 1
    balance = manager.balance()['available']
    monkeypatch.setattr(EverAITTSEngine, '_assemble_audio', original)
    monkeypatch.setattr(manager, 'ensure_active', lambda: pytest.fail('No new TTS, no active-license check'))
    result = SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert result.is_file() and len(calls) == 1
    assert manager.balance()['available'] == balance


def test_paid_chunk_ledger_recovers_when_manifest_write_was_lost(tmp_path, offline, monkeypatch):
    from src.everai import engine as core
    factory, calls, manager = offline
    instance = project(tmp_path, texts=('Xin chào.',))
    original = core.atomic_json
    blocked = [True]
    def fail_completed(path, data):
        if blocked[0] and data.get('completed'):
            raise OSError('offline disk failure after paid commit')
        original(path, data)
    monkeypatch.setattr(core, 'atomic_json', fail_completed)
    with pytest.raises(OSError):
        SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert len(calls) == 1 and manager.balance()['held'] == 0
    blocked[0] = False
    result = SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert result.is_file() and len(calls) == 1


def test_corrupt_fit_cache_is_rebuilt_without_tts(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    fit = instance.path(instance.data['cues'][0]['fit']['file'])
    fit.write_bytes(b'corrupted cache')
    result = SrtRunner(instance, lambda *a: pytest.fail('No TTS for fit cache'), None).process(tmp_path / 'again.wav', CONFIG)
    assert result.is_file() and len(calls) == 2
    assert audio.wave_frames(fit) == instance.data['cues'][0]['fit']['speech_frames']


def test_insufficient_wallet_blocks_all_new_tts(tmp_path, offline, monkeypatch):
    factory, calls, manager = offline
    instance = project(tmp_path)
    original = manager.balance
    monkeypatch.setattr(manager, 'balance', lambda **kw: dict(original(**kw), available=0))
    with pytest.raises(LicenseError, match='Không đủ'):
        SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    assert not calls and manager.balance()['held'] == 0


def test_generation_path_escape_is_rejected_on_reopen(tmp_path, offline):
    factory, calls, manager = offline
    instance = project(tmp_path)
    SrtRunner(instance, factory, manager).process(tmp_path / 'out.wav', CONFIG)
    path = instance.root / 'project.json'
    data = read_json(path)
    data['cues'][0]['generation']['raw_file'] = '../../outside.mp3'
    atomic_json(path, data)
    with pytest.raises(ValueError, match='Đường dẫn'):
        SrtProject(instance.root)


def test_bulk_edit_validates_revision_as_a_whole(tmp_path):
    instance = project(tmp_path)
    first, second = instance.data['cues']
    instance.edit_cues({first['id']: dict(text='A', start_ms=4100, end_ms=7100),
                        second['id']: dict(text='B', start_ms=100, end_ms=3100)})
    assert instance.cue(first['id'])['start_ms'] == 4100
    before = copy.deepcopy(instance.data)
    with pytest.raises(ValueError, match='chồng'):
        instance.edit_cues({first['id']: dict(text='invalid', start_ms=100, end_ms=7100)})
    assert instance.data == before
