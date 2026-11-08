"""Real Qt event loop and offline SRT generation/export with temporary wallets."""
import os
from pathlib import Path
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication
import pytest

from src.everai import qt_gui, utils
from src.everai.engine import EverAITTSEngine
from src.everai.tts.engine import MultiProviderTTSEngine
from src.everai.srt_studio import audio
from src.everai.srt_studio.store import SrtProject
from src.everai.client import EverAICancelled


def wait_for(app, condition, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.01)
    assert condition(), 'Qt workflow did not finish'


@pytest.fixture(scope='module')
def qtapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    if not QFontDatabase.families():
        for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
            font = Path('C:/Windows/Fonts') / name
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Segoe UI', 10))
    return app


@pytest.fixture
def studio(tmp_path, funded_license, qtapp, monkeypatch):
    window = qt_gui.StudioWindow(tmp_path / 'app', license_manager=funded_license)
    window.output_dir.setText(str(tmp_path / 'output'))
    monkeypatch.setattr(window, 'confirm', lambda *args: True)
    window.show()
    qtapp.processEvents()
    yield window
    if window.worker:
        window.stop_generation()
        wait_for(qtapp, lambda: window.worker is None)
    window.close()
    qtapp.processEvents()


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    pcm, mp3 = tmp_path / 'source.wav', tmp_path / 'source.mp3'
    with audio.open_writer(pcm) as writer:
        writer.writeframes(b'\x10\x00' * 24000)
    audio.run_ffmpeg(['-i', pcm, '-c:a', 'libmp3lame', mp3], threading.Event())
    sample = mp3.read_bytes()
    calls = []
    def render(self, idx, text, total, target, *args):
        calls.append((text, self.concurrency, idx))
        Path(target).write_bytes(sample)
        return Path(target)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    monkeypatch.setattr(MultiProviderTTSEngine, '_process_single_chunk', render)
    return calls


def import_srt(window, tmp_path, text='Xin chào.'):
    source = tmp_path / 'test.srt'
    source.write_text('1\n00:00:00,100 --> 00:00:02,000\n' + text +
                      '\n\n2\n00:00:03,000 --> 00:00:05,000\nHẹn gặp lại.', encoding='utf-8-sig')
    window.load_text_file(source)
    return source


def test_import_srt_keeps_normal_draft_and_everai_timestamps(studio, tmp_path):
    studio.editor.setPlainText('Bản nháp văn bản cần giữ.')
    import_srt(studio, tmp_path)
    assert studio.input_mode.currentData() == 'srt'
    assert studio.pages.currentIndex() == qt_gui.PAGE_SRT
    assert studio.editor.toPlainText() == 'Bản nháp văn bản cần giữ.'
    assert studio.srt_panel.project.data['cues'][0]['start_ms'] == 100
    assert studio.srt_panel.start.text() == '00:00:00,100'
    assert studio.srt_panel.model.rowCount() == 2
    studio.update_stats()
    assert '2 cue' in studio.srt_stats.text() and '-->' not in studio.srt_panel.text.toPlainText()


def test_qt_generates_wav_history_and_timestamp_edit_is_free(studio, renderer, tmp_path, qtapp, monkeypatch):
    import_srt(studio, tmp_path)
    studio.filename.setText('bài đọc.wav')
    studio.start_generation()
    assert studio.busy and not studio.srt_workers.isEnabled()
    wait_for(qtapp, lambda: studio.worker is None)
    assert studio.last_output_file and studio.last_output_file.name == 'bài đọc.wav'
    assert audio.wave_frames(studio.last_output_file) == 240000
    assert studio.store.recent[0]['project_id'] == studio.srt_panel.project.data['id']
    assert studio.store.recent[0]['format'] == 'wav'
    assert len(renderer) == 2
    studio.srt_panel.table.selectRow(1)
    studio.srt_panel.start.setText('00:00:04,000')
    studio.srt_panel.end.setText('00:00:06,000')
    # Offline fit/export is allowed even if license cannot render anymore.
    monkeypatch.setattr(studio.license_manager, 'ensure_active', lambda: pytest.fail('No license required for fit/export'))
    studio.start_generation()
    wait_for(qtapp, lambda: studio.worker is None)
    assert audio.wave_frames(studio.last_output_file) == 288000
    assert len(renderer) == 2
    assert studio.progress.value() == 1000


def test_project_reopen_and_mode_switch_preserve_drafts(studio, renderer, tmp_path, qtapp):
    studio.editor.setPlainText('Bản nháp text.')
    import_srt(studio, tmp_path)
    studio.start_generation()
    wait_for(qtapp, lambda: studio.worker is None)
    root, manager = studio.store.root, studio.license_manager
    project_root = studio.srt_panel.project.root
    studio.close()
    reopened = qt_gui.StudioWindow(root, license_manager=manager)
    try:
        assert reopened.srt_panel.project.root == project_root
        assert reopened.pages.currentIndex() == qt_gui.PAGE_SRT
        assert reopened.editor.toPlainText() == 'Bản nháp text.'
        reopened.navigate(qt_gui.PAGE_STUDIO)
        assert reopened.editor.toPlainText() == 'Bản nháp text.'
        reopened.navigate(qt_gui.PAGE_SRT)
        assert reopened.srt_panel.model.rowCount() == 2
    finally:
        reopened.close()


def test_srt_validation_blocks_tts_and_keeps_diagnostics(studio, renderer, tmp_path):
    source = tmp_path / 'bad.srt'
    source.write_text('1\nBAD\nXin chào', encoding='utf-8')
    studio.load_text_file(source)
    assert not studio.srt_panel.project
    assert 'dòng 2' in studio.status.text()
    assert not renderer and studio.worker is None


def test_overlap_edit_is_rejected_without_changing_saved_cue(studio, tmp_path):
    import_srt(studio, tmp_path)
    studio.srt_panel.end.setText('00:00:04,000')
    studio.start_generation()
    assert studio.worker is None
    assert 'chồng' in studio.status.text()
    assert studio.srt_panel.project.data['cues'][0]['end_ms'] == 2000


def test_regenerate_only_selected_cue(studio, renderer, tmp_path, qtapp):
    import_srt(studio, tmp_path)
    studio.start_generation()
    wait_for(qtapp, lambda: studio.worker is None)
    previous = studio.last_output_file
    cue_id = studio.srt_panel.selected()['id']
    studio.regenerate_srt_cue(cue_id)
    wait_for(qtapp, lambda: studio.worker is None)
    assert len(renderer) == 3
    assert studio.last_output_file == previous
    assert studio.srt_panel.project.data['state'] == 'pending'
    assert studio.recovery_frame.isVisible()


def test_srt_uses_independent_default_worker_count_and_persists_it(studio, renderer, tmp_path, qtapp, monkeypatch):
    concurrency = []
    original_init = qt_gui.SrtRunner.__init__
    def capture_init(self, *args, **kwargs):
        concurrency.append(args[3] if len(args) > 3 else kwargs.get('concurrency'))
        original_init(self, *args, **kwargs)
    monkeypatch.setattr(qt_gui.SrtRunner, '__init__', capture_init)
    assert studio.srt_workers.value() == 5
    studio.workers.setValue(2)
    import_srt(studio, tmp_path, 'Xin chào. ' * 75)
    studio.start_generation()
    wait_for(qtapp, lambda: studio.worker is None)
    assert renderer and concurrency == [5]
    studio.srt_workers.setValue(4)
    assert studio.save_state()
    assert utils.read_json(studio.store.root / 'settings.json')['srt_workers'] == 4


def test_close_cancels_project_workers_and_releases_wallet(studio, tmp_path, qtapp, monkeypatch):
    entered = threading.Event()
    def render(self, *args):
        entered.set()
        self.cancel_event.wait(5)
        raise EverAICancelled('offline cancelled')
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    import_srt(studio, tmp_path)
    studio.start_generation()
    wait_for(qtapp, entered.is_set)
    studio.close()
    assert studio.closing and studio.worker is not None
    wait_for(qtapp, lambda: studio.worker is None and not studio.isVisible())
    assert studio.license_manager.balance()['held'] == 0
    assert utils.read_json(studio.store.root / 'srt-draft.json')['project']


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_srt_panel_compact_layout_and_theme(studio, tmp_path, qtapp, theme):
    import_srt(studio, tmp_path)
    if studio.theme_mode != theme:
        studio.toggle_theme()
    studio.resize(900, 620)
    qtapp.processEvents()
    for widget in (studio.generate_button, studio.stop_button, studio.play_button):
        point = widget.mapTo(studio, widget.rect().bottomRight())
        assert point.x() < studio.width() and point.y() < studio.height()
    assert studio.srt_panel.table.isVisible()
    assert studio.srt_panel.output_format.isVisible()
    assert studio.srt_panel.table.height() >= 150
    assert studio.srt_panel.output_format.height() >= 36
    assert studio.editor_scroll.horizontalScrollBar().maximum() == 0
    assert studio.pages.currentIndex() == qt_gui.PAGE_SRT
    assert 'SRT' in studio.generate_button.text()


def test_unsaved_cue_edits_survive_selection_and_reopen(studio, tmp_path, qtapp):
    import_srt(studio, tmp_path)
    studio.srt_panel.text.setPlainText('Bản nháp cue chưa bấm Lưu.')
    studio.srt_panel.end.setText('not a timestamp yet')
    studio.srt_panel.table.selectRow(1)
    studio.srt_panel.text.setPlainText('Bản nháp cue thứ hai.')
    studio.srt_panel.table.selectRow(0)
    assert studio.srt_panel.text.toPlainText() == 'Bản nháp cue chưa bấm Lưu.'
    assert studio.srt_panel.end.text() == 'not a timestamp yet'
    assert studio.save_state()
    root, manager = studio.store.root, studio.license_manager
    studio.close()
    reopened = qt_gui.StudioWindow(root, license_manager=manager)
    try:
        assert reopened.srt_panel.text.toPlainText() == 'Bản nháp cue chưa bấm Lưu.'
        assert reopened.srt_panel.end.text() == 'not a timestamp yet'
        reopened.srt_panel.table.selectRow(1)
        assert reopened.srt_panel.text.toPlainText() == 'Bản nháp cue thứ hai.'
        assert reopened.srt_panel.project.data['cues'][0]['text'] == 'Xin chào.'
    finally:
        reopened.close()


def test_native_wav_player_uses_waveaudio_device(tmp_path):
    from src.everai.audio_player import MiniAudioPlayer
    wav = tmp_path / 'native.wav'
    with audio.open_writer(wav) as writer:
        writer.writeframes(b'\0\0' * 4800)
    player = MiniAudioPlayer(alias='EverAI_Srt_Test')
    if player._winmm is None:
        pytest.skip('Native player requires Windows')
    try:
        assert player.load(wav), player.last_error
        assert abs(player.get_duration_ms() - 100) <= 1
    finally:
        player.close()
