"""Offline desktop queue acceptance, using temporary data and signed test grants."""
import tkinter as tk
import uuid
import time
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.everai.gui import EverAIGuiApp
from src.everai.client import EverAICancelled
from src.everai.utils import read_json, ensure_ffmpeg
from src.everai.engine import EverAITTSEngine

pytestmark = pytest.mark.usefixtures('funded_license')


@pytest.fixture
def app(tmp_path):
    root = tk.Tk()
    root.withdraw()
    instance = EverAIGuiApp(root, tmp_path)
    instance.output_dir.set(str(tmp_path / 'output'))
    yield instance
    instance._destroy()


def item(name, text='Xin chào'):
    return dict(id=uuid.uuid4().hex, name=name, text=text, state='wait')


def test_queue_order_snapshot_and_reopen(app):
    app.txt_queue['items'] = [item('2.txt'), item('1.txt')]
    app.input_mode.set('queue')
    app._queue_refresh()
    selected = app.txt_queue['items'][1]['id']
    app.queue_tree.selection_set(selected)
    app._queue_move(-1)
    assert [i['name'] for i in app.txt_queue['items']] == ['1.txt', '2.txt']
    saved = read_json(app.store.root / 'txt_queue.json')
    assert saved['items'][0]['text'] == 'Xin chào'
    other_root = tk.Tk()
    other_root.withdraw()
    other = EverAIGuiApp(other_root, app.store.root)
    try:
        assert other.input_mode.get() == 'queue'
        assert other.txt_queue == saved
        assert not other.queue_running
    finally:
        other._destroy()


def test_queue_sequential_completion_and_fixed_config(app, tmp_path):
    app.txt_queue['items'] = [item('Chương 1.txt', 'Một'), item('Chương 2.txt', 'Hai')]
    app.input_mode.set('queue')
    calls = []
    def run(text, destination, config, job, preview):
        calls.append((text, destination, dict(config), job))
        app.busy = 'generation'
    with patch('src.everai.gui.ensure_ffmpeg', return_value=True), patch.object(app, '_new_engine', return_value=None), patch.object(app, '_run', side_effect=run), patch.object(app, '_load_result'):
        app._queue_start()
        assert len(calls) == 1
        original = calls[0][2]
        app.speed.set(1.7)
        first = tmp_path / 'first.mp3'
        first.write_bytes(b'audio')
        app._finish(first, None, calls[0][3], False)
        assert len(calls) == 2
        assert calls[1][0] == 'Hai'
        assert calls[1][2] == original
        assert calls[0][3] != calls[1][3]
        second = tmp_path / 'second.mp3'
        second.write_bytes(b'audio')
        app._finish(second, None, calls[1][3], False)
        assert not app.queue_running
        assert [i['state'] for i in app.txt_queue['items']] == ['done', 'done']


def test_queue_stop_resume_reuses_job_and_skip(app):
    app.txt_queue['items'] = [item('1.txt'), item('2.txt')]
    app.input_mode.set('queue')
    with patch('src.everai.gui.ensure_ffmpeg', return_value=True), patch.object(app, '_new_engine', return_value=None), patch.object(app, '_run') as run:
        app._queue_start()
        job = run.call_args.args[3]
        app._stop_generation()
        app._finish(None, EverAICancelled(), job, False)
        assert run.call_count == 1
        assert not app.queue_running
        app._queue_start()
        assert run.call_args.args[3] == job
        app.queue_running = False
        app._finish(None, ValueError('offline'), job, False)
        app.queue_tree.selection_set(app.txt_queue['items'][0]['id'])
        app._queue_skip()
        app._queue_start()
        assert run.call_args.args[0] == app.txt_queue['items'][1]['text']
        assert app.txt_queue['items'][0]['state'] == 'skipped'


def test_import_dialog_natural_sort_reorder_validation_and_cancel(app, tmp_path):
    paths = []
    for name, text in [('10.txt', 'Mười'), ('2.txt', 'Hai'), ('empty.txt', '')]:
        path = tmp_path / name
        path.write_text(text, encoding='utf-8-sig')
        paths.append(str(path))
    with patch('src.everai.gui.filedialog.askopenfilenames', return_value=paths):
        app._queue_import()
    dialog = next(w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == 'Duyệt thứ tự file TXT')
    tree = next(w for w in dialog.winfo_children() if isinstance(w, __import__('tkinter.ttk', fromlist=['Treeview']).Treeview))
    buttons = {w.cget('text'): w for frame in dialog.winfo_children() for w in frame.winfo_children() if isinstance(w, __import__('tkinter.ttk', fromlist=['Button']).Button)}
    assert [tree.item(i, 'values')[1] for i in tree.get_children()] == ['2.txt', '10.txt', 'empty.txt']
    with patch('src.everai.gui.messagebox.showerror') as error:
        buttons['Đưa vào hàng đợi'].invoke()
        error.assert_called_once()
    tree.selection_set(tree.get_children()[-1])
    buttons['Bỏ file'].invoke()
    tree.selection_set(tree.get_children()[1])
    buttons['↑'].invoke()
    buttons['Đưa vào hàng đợi'].invoke()
    assert [i['name'] for i in app.txt_queue['items']] == ['10.txt', '2.txt']
    assert app.txt_queue['items'][0]['text'] == 'Mười'
    Path(paths[0]).write_text('Đã thay đổi', encoding='utf-8')
    assert app.txt_queue['items'][0]['text'] == 'Mười'


def test_real_worker_handoff_finishes_before_next_file(app, tmp_path):
    calls = []
    class LocalEngine:
        is_cancelled = False
        def process(self, text, destination, **kwargs):
            calls.append(text)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b'local-test-audio')
            return destination
        def cancel(self):
            self.is_cancelled = True
    app.txt_queue['items'] = [item('1.txt', 'Một'), item('2.txt', 'Hai')]
    app.input_mode.set('queue')
    with patch('src.everai.gui.ensure_ffmpeg', return_value=True), patch.object(app, '_new_engine', side_effect=lambda **kw: LocalEngine()), patch.object(app, '_load_result'):
        app._queue_start()
        deadline = time.monotonic() + 8
        while app.queue_running and time.monotonic() < deadline:
            app.root.update()
            time.sleep(.01)
    assert calls == ['Một', 'Hai']
    assert not app.queue_running
    assert app.thread is None
    assert all(i['state'] == 'done' and Path(i['result']).is_file() for i in app.txt_queue['items'])


def test_queue_engine_wallet_and_ffmpeg_publish(app, tmp_path):
    ffmpeg = ensure_ffmpeg()
    assert ffmpeg, 'Release runtime requires FFmpeg'
    sample = tmp_path / 'fixture.mp3'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'anullsrc=r=44100:cl=mono', '-t', '0.15', '-y', str(sample)], check=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    audio = sample.read_bytes()
    app.txt_queue['items'] = [item('Cùng tên.txt', 'Một'), item('Cùng tên.txt', 'Hai')]
    app.input_mode.set('queue')
    before = app.license_manager.balance()['used']
    def local_chunk(engine, index, text, total, output, *args):
        output.write_bytes(audio)
        return output
    with patch.object(EverAITTSEngine, '_process_single_chunk', local_chunk), patch.object(app, '_load_result'):
        app._queue_start()
        deadline = time.monotonic() + 15
        while app.queue_running and time.monotonic() < deadline:
            app.root.update()
            time.sleep(.01)
    assert not app.queue_running
    assert all(i['state'] == 'done' for i in app.txt_queue['items'])
    results = [Path(i['result']) for i in app.txt_queue['items']]
    assert results[0] != results[1]
    assert all(p.stat().st_size > 0 for p in results)
    assert app.license_manager.balance()['used'] - before == len('Một') + len('Hai')
    for i in app.txt_queue['items']:
        manifest = read_json(app.store.root / 'jobs' / i['job'] / 'manifest.json')
        assert manifest['status'] == 'completed'
