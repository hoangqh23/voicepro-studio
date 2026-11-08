"""Desktop and branded-build regression checks, isolated from provider/user data."""
import json
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
import types
from unittest.mock import patch

import pytest

from src.everai import gui, utils
from src.everai.build_voicepro import brand_gui_source
from src.everai.client import EverAICancelled
from src.everai.engine import EverAITTSEngine
from src.everai.tts.engine import MultiProviderTTSEngine


@pytest.fixture
def app(tmp_path, funded_license):
    root = tk.Tk()
    root.withdraw()
    instance = gui.EverAIGuiApp(root, tmp_path / 'app')
    instance.output_dir.set(str(tmp_path / 'out'))
    yield instance
    instance._destroy()


def test_voicepro_first_start_restore_and_reopen_keep_canonical_config(tmp_path):
    source = Path(gui.__file__).read_text(encoding='utf-8')
    branded = types.ModuleType('src.everai._voicepro_test')
    branded.__file__ = gui.__file__
    branded.__package__ = 'src.everai'
    exec(compile(brand_gui_source(source), gui.__file__, 'exec'), branded.__dict__)
    for _ in range(2):
        root = tk.Tk()
        root.withdraw()
        instance = branded.EverAIGuiApp(root, tmp_path)
        try:
            assert instance.tts_provider.get() == 'Voice Pro - chất lượng cao nhất'
            assert instance._provider_id() == 'everai'
            assert instance.model.get() == 'VoicePro-v1.6'
            assert instance._snapshot()['model_id'] == 'everai-v1.6'
            assert instance._snapshot()['speaker_id'] == 'vi_male_leduc_mb'
            assert instance.cbo_voice.cget('values')
            with patch.object(instance, '_preview_configured') as online_preview, \
                    patch.object(branded, 'voice_demo_path', return_value=None):
                instance._preview()
                online_preview.assert_not_called()
            instance._save()
            saved = utils.read_json(tmp_path / 'settings.json')
            assert saved['tts_provider'] == 'everai'
            assert saved['model_id'] == 'everai-v1.6'
        finally:
            instance._destroy()


def make_job(app, job_id, text='recovered text'):
    config = app._snapshot()
    job = app.store.root / 'jobs' / job_id
    utils.atomic_json(job / 'manifest.json', dict(text=text, config=config,
        completed={}, status='interrupted', created_at=time.time(), output_file='old.mp3'))
    return job, config


def test_continue_finished_queue_restores_real_job_and_honors_cancel(app):
    job, config = make_job(app, 'standalone')
    app.txt_queue['items'] = [dict(id='done', name='done.txt', text='done', state='done')]
    app.input_mode.set('queue')
    app.txt_input.insert('1.0', 'current draft')
    app.refresh_recovery()
    assert app.pending_job == job
    with patch('src.everai.gui.messagebox.askyesno', return_value=False), \
            patch.object(app, '_start_generation') as start:
        app._continue()
        start.assert_not_called()
        assert app.txt_input.get('1.0', 'end-1c') == 'current draft'
    with patch('src.everai.gui.messagebox.askyesno', return_value=True), \
            patch.object(app, '_start_generation') as start:
        app._continue()
        start.assert_called_once_with(resume=True)
    assert app.input_mode.get() == 'text'
    assert app.pending_job == job
    assert app.txt_input.get('1.0', 'end-1c') == 'recovered text'
    assert app._snapshot() == config


def test_recovery_ignores_skipped_queue_job_and_prefers_actual_queue(app):
    old, _ = make_job(app, 'skipped')
    active, _ = make_job(app, 'active')
    make_job(app, 'newer-unrelated')
    app.txt_queue['items'] = [
        dict(id='s', name='s.txt', text='s', state='skipped', job=old.name),
        dict(id='a', name='a.txt', text='a', state='error', job=active.name)]
    app.input_mode.set('queue')
    app.refresh_recovery()
    assert app.pending_job == active
    with patch.object(app, '_queue_start') as start:
        app._continue()
        start.assert_called_once()


def test_skipping_last_failed_file_immediately_clears_recovery_banner(app):
    job, _ = make_job(app, 'skip-last')
    app.txt_queue['items'] = [dict(id='s', name='s.txt', text='s', state='error', job=job.name)]
    app.input_mode.set('queue')
    app._queue_refresh()
    assert app.pending_job == job
    app.queue_tree.selection_set('s')
    app._queue_skip()
    assert app.pending_job is None
    assert not app.recovery.winfo_manager()


@pytest.mark.parametrize('count,size', [(10, 41), (2, 700)])
def test_srt_stats_and_engine_share_exact_chunks(app, count, size):
    text = '\n\n'.join(f'{i+1}\n00:{i//30:02d}:{i*2%60:02d},000 --> '
                      f'00:{i//30:02d}:{(i*2+1)%60:02d},000\n' + 'A'*(size-1)+'.'
                      for i in range(count))
    expected = MultiProviderTTSEngine().split_text(text)
    assert gui.speech_chunks(text, 'edge') == expected
    app.tts_provider.set('edge')
    app._provider_changed()
    app.txt_input.insert('1.0', text)
    app._update_stats()
    assert f'{len(expected)} đoạn' in app.stats.get()
    assert f'{sum(map(len, expected)):,} ký tự tính phí' in app.stats.get()


@pytest.mark.parametrize('reason', ['cancel', 'timeout'])
def test_merge_cancel_or_timeout_keeps_paid_chunks_for_resume(tmp_path, funded_license, reason):
    engine = EverAITTSEngine(license_manager=funded_license)
    job, output = tmp_path / 'job', tmp_path / 'out.mp3'
    calls = []

    def render(idx, text, total, path, *args):
        calls.append(text)
        path.write_bytes(b'ID3offline chunk')
        return path

    def blocked_merge(results, target):
        utils.run_audio_command([sys.executable, '-c', 'import time; time.sleep(30)'],
            cancel_event=engine.cancel_event, timeout=5 if reason == 'cancel' else .25)

    timer = threading.Timer(.25, engine.cancel)
    if reason == 'cancel':
        timer.start()
    started = time.monotonic()
    try:
        with patch.object(engine, '_process_single_chunk', side_effect=render), \
                patch.object(engine, '_assemble_audio', side_effect=blocked_merge):
            with pytest.raises(EverAICancelled if reason == 'cancel' else utils.AudioAssemblyError):
                engine.process('paid text', output, job_dir=job)
    finally:
        timer.cancel()
        if reason == 'cancel':
            timer.join()
    assert time.monotonic() - started < 5
    assert utils.read_json(job / 'manifest.json')['status'] == 'interrupted'
    assert len(list(job.glob('chunk_*.mp3'))) == 1
    balance = funded_license.balance()
    assert balance['used'] == len('paid text') and balance['held'] == 0

    resumed = EverAITTSEngine(license_manager=funded_license)
    def merge(results, target):
        target.write_bytes(results[0].read_bytes())
        return target
    with patch.object(resumed, '_process_single_chunk', side_effect=AssertionError('must reuse paid chunk')), \
            patch.object(resumed, '_assemble_audio', side_effect=merge):
        assert resumed.process('paid text', output, job_dir=job).is_file()
    assert funded_license.balance()['used'] == len('paid text')
    assert len(calls) == 1


def test_pre_cancelled_audio_command_never_starts_process():
    event = threading.Event()
    event.set()
    with patch('src.everai.utils.subprocess.Popen') as popen:
        with pytest.raises(EverAICancelled):
            utils.run_audio_command(['unused'], cancel_event=event)
        popen.assert_not_called()
