"""Qt frontend workflows, real Qt event loop, temporary wallets and offline audio."""
import json
import os
from pathlib import Path
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QAbstractButton, QLabel, QScrollArea
import pytest

from src.everai import qt_gui, utils
from src.everai.client import EverAIError, EverAICancelled
from src.everai.engine import EverAITTSEngine
from src.everai.tts.engine import MultiProviderTTSEngine


@pytest.fixture(scope='module')
def qtapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    if not QFontDatabase.families():
        for filename in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
            path = Path('C:/Windows/Fonts') / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Segoe UI', 10))
    yield app


def wait_until(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'Qt event loop did not reach expected state'


@pytest.fixture
def window(qtapp, tmp_path, funded_license):
    data = tmp_path / 'app'
    utils.atomic_json(data / 'proxies.json', dict(version=1, items=[]))
    instance = qt_gui.StudioWindow(data, license_manager=funded_license)
    instance.output_dir.setText(str(tmp_path / 'audio'))
    instance.show()
    qtapp.processEvents()
    yield instance
    if instance.worker:
        instance.stop_generation()
        wait_until(qtapp, lambda: instance.worker is None)
    instance.close()
    qtapp.processEvents()


def test_customer_ui_hides_all_proxy_controls(window):
    assert not window.windowIcon().isNull()
    assert not window.brand_avatar.pixmap().isNull()
    assert [item.text() for item in window.nav_buttons] == [
        'Studio giọng nói', 'Hàng đợi TXT', 'SRT', 'Lịch sử', 'Cài đặt'
    ]
    assert window.pages.count() == 5
    assert not hasattr(window, 'proxy_table')
    assert not hasattr(window, 'traffic_table')
    visible_text = [widget.text() for widget in
                    window.findChildren(QAbstractButton) + window.findChildren(QLabel)
                    if widget.isVisible()]
    assert not any('proxy' in text.casefold() for text in visible_text)


def test_favorite_voice_toggle_filters_and_reopen(window):
    original = [window.voice.itemData(i) for i in range(window.voice.count())]
    selected = original[-1]
    window.voice.setCurrentIndex(window.voice.findData(selected))
    config = window.snapshot()
    window.favorite_button.click()
    assert window.voice.itemData(0) == selected
    assert window.voice.currentData() == selected
    assert window.snapshot() == config
    assert window.favorite_button.text() == '★'
    assert window.voice.itemText(0).startswith('★ ')
    window.search.setText('no-voice-matches-this-query')
    assert window.voice.count() == 0
    assert not window.favorite_button.isEnabled()
    window.search.clear()
    window.provider.setCurrentIndex(window.provider.findData('capcut'))
    assert all(str(window.voice.itemData(i)).startswith('capcut|') for i in range(window.voice.count()))
    window.provider.setCurrentIndex(window.provider.findData('everai'))
    assert window.voice.itemData(0) == selected
    reopened = qt_gui.StudioWindow(window.store.root, license_manager=window.license_manager)
    try:
        assert selected in reopened.favorite_voices
        assert reopened.voice.itemData(0) == selected
        reopened.voice.setCurrentIndex(reopened.voice.findData(selected))
        reopened.favorite_button.click()
        assert selected not in reopened.favorite_voices
        assert [reopened.voice.itemData(i) for i in range(reopened.voice.count())] == original
        assert reopened.voice.currentData() == selected
        assert reopened.favorite_button.text() == '☆'
    finally:
        reopened.close()


def test_favorite_voice_save_failure_preserves_state(window, monkeypatch):
    selected = window.voice.currentData()
    def fail(*args, **kwargs):
        raise OSError('read only')
    monkeypatch.setattr(qt_gui, 'atomic_json', fail)
    window.favorite_button.click()
    assert selected not in window.favorite_voices
    assert window.favorite_button.text() == '☆'
    assert not window.favorite_button.isChecked()


def test_theme_toggle_persist_and_reopen_without_rewriting_wallet(window, qtapp):
    assert window.theme_mode == 'dark'
    balance = window.license_manager.balance()
    window.toggle_theme()
    assert window.theme_mode == 'light'
    assert utils.read_json(window.store.root / 'qt-ui.json')['theme'] == 'light'
    data = window.store.root
    manager = window.license_manager
    window.close()
    reopened = qt_gui.StudioWindow(data, license_manager=manager)
    try:
        assert reopened.theme_mode == 'light'
        assert reopened.license_manager.balance() == balance
        reopened.toggle_theme()
        assert reopened.theme_mode == 'dark'
    finally:
        reopened.close()


@pytest.mark.parametrize('size', [(900, 620), (1280, 850), (1500, 950)])
def test_layout_keeps_generate_and_player_inside_window(window, qtapp, size):
    window.resize(*size)
    qtapp.processEvents()
    assert window.width() <= size[0]
    assert window.height() <= size[1]
    for index in range(window.pages.count()):
        window.navigate(index)
        qtapp.processEvents()
        for widget in (window.generate_button, window.play_button, window.seek, window.theme_button):
            point = widget.mapTo(window, widget.rect().bottomRight())
            assert point.x() < window.width() and point.y() < window.height()
            assert widget.isVisible()


def test_voice_panel_no_horizontal_overflow_and_advanced_disclosure(window, qtapp):
    for theme in ('dark', 'light'):
        if window.theme_mode != theme:
            window.toggle_theme()
        for width, height in ((900, 620), (1280, 850)):
            window.resize(width, height)
            qtapp.processEvents()
            area = window.voice.parentWidget()
            while area and not isinstance(area, QScrollArea):
                area = area.parentWidget()
            assert area.horizontalScrollBar().maximum() == 0
            for field in (window.provider, window.voice, window.model, window.speed, window.workers):
                assert field.height() >= 36
    assert not window.advanced_panel.isVisible()
    window.advanced_button.click()
    qtapp.processEvents()
    assert window.pitch.isVisible() and window.bitrate.isVisible()
    window.advanced_button.click()
    assert not window.advanced_panel.isVisible()


def test_inline_empty_text_validation_and_edit_recovery(window, qtapp):
    window.editor.clear()
    window.start_generation()
    qtapp.processEvents()
    assert window.text_error.isVisible()
    assert window.editor.property('invalid')
    assert window.editor.hasFocus()
    window.editor.setPlainText('Nội dung hợp lệ')
    assert not window.text_error.isVisible()
    assert not window.editor.property('invalid')


def test_theme_text_contrast():
    from src.everai.qt_theme import COLORS
    def luminance(value):
        channels = [int(value[i:i+2], 16) / 255 for i in (1, 3, 5)]
        channels = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*w for v, w in zip(channels, (.2126, .7152, .0722)))
    def contrast(a, b):
        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high+.05)/(low+.05)
    for colors in COLORS.values():
        for background in ('bg', 'surface', 'raised', 'sidebar', 'ambient', 'ambient_end'):
            for foreground in ('text', 'muted'):
                assert contrast(colors[foreground], colors[background]) >= 4.5
        for background in ('accent', 'primary_end'):
            assert contrast(colors['on_primary'], colors[background]) >= 4.5


def test_supported_providers_keep_canonical_ids_and_removed_providers_are_absent(window):
    assert window.windowTitle() == 'Voice Pro - chất lượng cao nhất'
    assert [item.text() for item in window.nav_buttons] == [
        'Studio giọng nói', 'Hàng đợi TXT', 'SRT', 'Lịch sử', 'Cài đặt']
    assert not hasattr(window, 'route_label')
    assert not hasattr(window, 'api_key')
    for removed in ('elevenlabs', 'edge'):
        assert window.provider.findData(removed) == -1
    for provider in ('everai', 'saydi', 'capcut'):
        window.search.clear()
        window.provider.setCurrentIndex(window.provider.findData(provider))
        assert window.voice.count() > 0
        config = window.snapshot()
        if provider == 'everai':
            assert config['model_id'].startswith('everai-')
        else:
            assert config['speaker_id'].startswith(provider + '|')
        window.session_id.setText('test-session-secret')
        window.saydi_token.setText('test-saydi-secret')
        assert window.save_state()
    for path in window.store.root.rglob('*.json'):
        raw = path.read_text('utf-8')
        assert 'test-session-secret' not in raw
        assert 'test-saydi-secret' not in raw
    assert window.snapshot()['speaker_id'].startswith('capcut|')
    assert not window.pitch.isEnabled() and not window.bitrate.isEnabled()


def test_white_label_ids_restore_without_changing_fingerprint(window, monkeypatch):
    monkeypatch.setattr(qt_gui, 'PRIMARY_MODEL_PREFIX', 'VoicePro-')
    window.provider_changed()
    assert window.model.currentText() == 'VoicePro-v1.6'
    assert window.snapshot()['model_id'] == 'everai-v1.6'
    config = window.snapshot()
    window.restore_config(config)
    assert window.snapshot() == config


def test_capcut_and_saydi_use_direct_engines(window, monkeypatch):
    window.provider.setCurrentIndex(window.provider.findData('capcut'))
    engine = window.new_engine()
    assert isinstance(engine, MultiProviderTTSEngine) and engine.proxy_catalog is None
    text = '1\n00:00:00,000 --> 00:00:03,000\n' + 'Xin chào. ' * 70
    chunks = qt_gui.speech_chunks(text, 'capcut')
    assert max(map(len, chunks)) <= 450
    window.editor.setPlainText(text)
    window.update_stats()
    assert f'{len(chunks)} phân đoạn' in window.stats.text()
    monkeypatch.delenv('SAYDI_ACCESS_TOKEN', raising=False)
    saydi = window.new_engine('saydi')
    assert isinstance(saydi, MultiProviderTTSEngine) and saydi.proxy_catalog is None
    assert not saydi.service.saydi_token


def test_everai_preview_is_offline_and_keeps_main_result(window, monkeypatch, tmp_path):
    result = tmp_path / 'result.mp3'
    result.write_bytes(b'ID3' + b'x' * 200)
    window.last_output_file = result
    monkeypatch.setattr(window, 'new_engine', lambda *a: pytest.fail('Offline preview must not create an engine'))
    loaded = []
    monkeypatch.setattr(window, 'load_playback', lambda path, name=None: loaded.append(Path(path)))
    monkeypatch.setattr(window, 'play_pause', lambda: None)
    window.preview()
    assert loaded and loaded[0].parent.name == 'voice_samples'
    assert window.last_output_file == result


def test_worker_finishes_before_unlock_and_history_publish(window, qtapp, monkeypatch):
    sample = utils.voice_demo_path('vi_male_leduc_mb').read_bytes()
    def render(self, idx, text, total, target, *args):
        time.sleep(.03)
        Path(target).write_bytes(sample)
        return Path(target)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    window.editor.setPlainText('Đây là một bài kiểm tra giao diện PyQt6, không gọi dịch vụ thật.')
    window.start_generation()
    assert window.busy and not window.provider.isEnabled()
    assert window.worker is not None
    wait_until(qtapp, lambda: window.worker is None)
    assert not window.busy and window.provider.isEnabled()
    assert window.last_output_file.is_file()
    assert window.progress.value() == 1000
    assert window.store.recent[0]['path'] == str(window.last_output_file)
    assert window.license_manager.balance()['held'] == 0


def test_close_waits_for_cancelled_worker_and_preserves_draft(window, qtapp, monkeypatch):
    def render(self, *args):
        self.cancel_event.wait(3)
        raise EverAICancelled('stopped')
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    monkeypatch.setattr(window, 'confirm', lambda *a: True)
    window.editor.setPlainText('Bản nháp cần giữ sau khi đóng cửa sổ.')
    window.start_generation()
    window.close()
    assert window.closing and window.worker is not None
    wait_until(qtapp, lambda: window.worker is None and not window.isVisible())
    assert utils.read_json(window.store.root / 'draft.json')['text'].startswith('Bản nháp')
    assert window.license_manager.balance()['held'] == 0


def test_queue_snapshot_config_and_completion_no_recovery_banner(window, qtapp, monkeypatch, tmp_path):
    sample = utils.voice_demo_path('vi_male_leduc_mb').read_bytes()
    calls = []
    def render(self, idx, text, total, target, speaker_id, *args):
        calls.append((text, speaker_id))
        Path(target).write_bytes(sample)
        return Path(target)
    monkeypatch.setattr(EverAITTSEngine, '_process_single_chunk', render)
    one, two = tmp_path / '1.txt', tmp_path / '2.txt'
    one.write_text('Nội dung một.', encoding='utf-8')
    two.write_text('Nội dung hai.', encoding='utf-8')
    assert window.add_queue_files([two, one])
    one.write_text('Thay đổi file gốc không thay snapshot.', encoding='utf-8')
    speaker = window.snapshot()['speaker_id']
    window.queue_start()
    wait_until(qtapp, lambda: not window.busy and not window.queue_running)
    assert [x[0] for x in calls] == ['Nội dung một.', 'Nội dung hai.']
    assert all(x[1] == speaker for x in calls)
    assert all(i['state'] == 'done' for i in window.txt_queue['items'])
    assert not window.recovery_frame.isVisible()
    assert all(Path(i['result']).is_file() for i in window.txt_queue['items'])


def test_resume_requires_confirmation_before_replacing_draft(window, monkeypatch):
    job = window.store.root / 'jobs' / 'unfinished'
    config = window.snapshot()
    utils.atomic_json(job / 'manifest.json', dict(text='Nội dung cũ', config=config,
                     status='interrupted', completed={}, output_file=str(window.store.root / 'out.mp3')))
    window.editor.setPlainText('Nội dung mới chưa muốn thay.')
    window.refresh_recovery()
    monkeypatch.setattr(window, 'confirm', lambda *a: False)
    window.resume_generation()
    assert window.editor.toPlainText() == 'Nội dung mới chưa muốn thay.'
    assert window.worker is None


def test_queue_skip_filters_recovery_and_new_items_capture_new_config(window, tmp_path):
    source = tmp_path / 'input.txt'
    source.write_text('Một đoạn văn.', encoding='utf-8')
    window.add_queue_files([source])
    item = window.txt_queue['items'][0]
    item.update(job='old-queue-job', state='error')
    utils.atomic_json(window.store.root / 'jobs/old-queue-job/manifest.json', dict(
        text=item['text'], config=window.snapshot(), status='interrupted', completed={}))
    window.queue_table.selectRow(0)
    window.queue_skip()
    assert item['state'] == 'skipped' and window.pending_job is None


def test_removed_provider_voice_is_not_restored_as_runnable(window):
    with pytest.raises(ValueError, match='không còn trong catalog'):
        window.restore_config(dict(speaker_id='edge|vi-VN-HoaiMyNeural', model_id='Edge Neural',
                                   speed=1.0, pitch=1.0, bitrate='128 kbps'))
