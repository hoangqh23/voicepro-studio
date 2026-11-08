"""PyQt6 desktop Studio. Engines, wallets and file contracts stay in the core."""
import copy
import csv
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import uuid
from collections import deque

from PyQt6.QtCore import Qt, QTimer, QUrl, QThread
from PyQt6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QPlainTextEdit,
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QSlider,
    QProgressBar, QStackedWidget, QSplitter, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QInputDialog, QTabWidget, QSizePolicy)

try:
    from .srt_studio.store import SrtProject, project_lock as srt_project_lock
    from .srt_studio.runner import SrtRunner
    from .srt_studio.qt_panel import SrtPanel
    from .srt_studio.model import chunks_for as srt_chunks_for
except (ImportError, ModuleNotFoundError) as _srt_err:
    if getattr(_srt_err, 'name', None) not in (None, 'srt_studio', 'everai.srt_studio', ''):
        raise
    from srt_studio.store import SrtProject, project_lock as srt_project_lock
    from srt_studio.runner import SrtRunner
    from srt_studio.qt_panel import SrtPanel
    from srt_studio.model import chunks_for as srt_chunks_for

try:
    from .engine import EverAITTSEngine
    from .client import EverAICancelled, EverAIQuotaError
    from .license_manager import LicenseManager, LicenseError, MAX_LICENSE_BYTES, expiry_label
    from .proxy_manager import ProxyCatalog, ProxyItem
    from .audio_player import MiniAudioPlayer
    from .utils import (StudioStore, application_data_dir, atomic_json, read_json,
        writable_directory, audio_filename, split_text_into_chunks, normalize_tts_text,
        parse_srt_file, voice_demo_path, ensure_ffmpeg, redact_log, parse_proxy,
        password_hash, verify_password, AudioAssemblyError)
    from .voices import EVERAI_VOICES
    from .tts.service import studio_voices, split_voice_id, TTSService
    from .tts.engine import MultiProviderTTSEngine
    from .tts.saydi import CHUNK_LIMIT
    from .tts.srt import split_srt_chunks
    from .tts.languages import language_label
    from .qt_theme import apply_theme, make_icon
    from .voice_picker import VoicePicker
except ImportError:
    from engine import EverAITTSEngine
    from client import EverAICancelled, EverAIQuotaError
    from license_manager import LicenseManager, LicenseError, MAX_LICENSE_BYTES, expiry_label
    from proxy_manager import ProxyCatalog, ProxyItem
    from audio_player import MiniAudioPlayer
    from utils import (StudioStore, application_data_dir, atomic_json, read_json,
        writable_directory, audio_filename, split_text_into_chunks, normalize_tts_text,
        parse_srt_file, voice_demo_path, ensure_ffmpeg, redact_log, parse_proxy,
        password_hash, verify_password, AudioAssemblyError)
    from voices import EVERAI_VOICES
    from tts.service import studio_voices, split_voice_id, TTSService
    from tts.engine import MultiProviderTTSEngine
    from tts.saydi import CHUNK_LIMIT
    from tts.srt import split_srt_chunks
    from tts.languages import language_label
    from qt_theme import apply_theme, make_icon
    from voice_picker import VoicePicker

APP_TITLE = 'Voice Pro - chất lượng cao nhất'
PRIMARY_PROVIDER_LABEL = 'Voice Pro - chất lượng cao nhất'
PRIMARY_MODEL_PREFIX = 'everai-'
STUDIO_VOICES = [dict(v, engine='everai', lang='vi') for v in EVERAI_VOICES] + studio_voices()
SAMPLE_TEXT = 'Xin chào, chúc bạn một ngày tốt lành. Đây là giọng đọc bạn đang lựa chọn.'
PROVIDER_LABELS = [('everai', PRIMARY_PROVIDER_LABEL), ('capcut', 'CapCut'), ('saydi', 'Saydi')]
PAGE_STUDIO, PAGE_QUEUE, PAGE_SRT, PAGE_HISTORY, PAGE_SETTINGS = range(5)
BRAND_LOGO = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets/branding/voicepro-logo.png'


def speech_chunks(text, provider):
    split = lambda value: split_text_into_chunks(
        normalize_tts_text(value), max_length=CHUNK_LIMIT if provider == 'saydi' else 450)
    return split_srt_chunks(text, split)[0] if provider != 'everai' and '-->' in text else split(text)


def label(text, role=None):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback=None, role=None):
    widget = QPushButton(text)
    if role:
        widget.setObjectName(role)
    if callback:
        widget.clicked.connect(lambda checked=False: callback())
    return widget


def card(title=None):
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(13)
    if title:
        layout.addWidget(label(title, 'section'))
    return frame, layout


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setShowGrid(False)
    widget.setAlternatingRowColors(True)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return widget


class TaskThread(QThread):
    """Only the finished signal triggers UI completion, after run() has returned."""
    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.function()
        except Exception as error:
            self.error = error


class StudioWindow(QMainWindow):
    def __init__(self, data_dir=None, license_manager=None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(BRAND_LOGO)))
        self.resize(1280, 850)
        self.setMinimumSize(900, 620)
        self.store = StudioStore(data_dir or application_data_dir())
        self.license_error = ''
        try:
            self.license_manager = license_manager or LicenseManager(self.store.root)
        except (ValueError, OSError) as error:
            self.license_manager = None
            self.license_error = str(error)
        self.ui_queue = queue.Queue()
        self.log_buffer = deque(maxlen=2000)
        self.logger = logging.getLogger('everai.qt.' + uuid.uuid4().hex)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        logs = self.store.root / 'logs'
        logs.mkdir(exist_ok=True)
        self.log_handler = RotatingFileHandler(logs / 'studio.log', maxBytes=1024*1024,
                                               backupCount=3, encoding='utf-8')
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(self.log_handler)
        self.proxy_catalog = ProxyCatalog(self.store.root / 'proxies.json', self.log)
        self.player = MiniAudioPlayer(alias='EverAIQt_' + uuid.uuid4().hex)
        self.worker = None
        self.network_worker = None
        self.network_cancel = threading.Event()
        self.engine = None
        self.busy = False
        self.closing = False
        self.restoring = True
        self.preview_running = False
        self.pending_job = None
        self.active_job = None
        self.queue_current = None
        self.queue_running = False
        self.last_output_file = None
        self.playback_file = None
        self.source_name = ''
        self.last_error_code = ''
        self.txt_queue = self._load_queue()
        favorites = self.store.load('favorite-voices.json', [])
        self.favorite_voices = {value for value in favorites if isinstance(value, str)} if isinstance(favorites, list) else set()
        ui_settings = self.store.load('qt-ui.json', {})
        self.theme_mode = ui_settings.get('theme', 'dark') if isinstance(ui_settings, dict) else 'dark'
        if self.theme_mode not in ('light', 'dark'):
            self.theme_mode = 'dark'
        self.controls = []
        self._build_ui()
        for field in self.findChildren(QComboBox) + self.findChildren(QSpinBox) + self.findChildren(QDoubleSpinBox):
            field.setMinimumHeight(36)
        for combo in self.findChildren(QComboBox):
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._restore()
        apply_theme(QApplication.instance(), self.theme_mode)
        self.apply_icons()
        self.restoring = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.update_stats)
        self.save_timer.timeout.connect(self.save_state)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(100)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self.balance_timer = QTimer(self)
        self.balance_timer.setInterval(1500)
        self.balance_timer.timeout.connect(self.refresh_balance)
        self.balance_timer.start()
        self.refresh_balance()
        self.refresh_history()
        self.refresh_queue()
        self.restore_srt_draft()
        self.refresh_recovery()
        for sequence, callback in [('Ctrl+Return', self.start_generation), ('Ctrl+O', self.import_text),
                                    ('Ctrl+Shift+O', self.queue_import)]:
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(lambda checked=False, fn=callback: fn())
            self.addAction(action)

    def _load_queue(self):
        value = self.store.load('txt_queue.json', dict(items=[], config=None))
        if not isinstance(value, dict) or not isinstance(value.get('items'), list):
            return dict(items=[], config=None)
        value['items'] = [i for i in value['items'] if isinstance(i, dict)
                          and all(isinstance(i.get(k), str) for k in ('id', 'name', 'text', 'state'))]
        for item in value['items']:
            if item['state'] == 'running':
                item['state'] = 'error'
        return value

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        base = QHBoxLayout(root)
        base.setContentsMargins(0, 0, 0, 0)
        base.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(185)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(18, 28, 18, 22)
        nav.setSpacing(9)
        self.brand_avatar = QLabel()
        self.brand_avatar.setAccessibleName('Logo Voice Pro')
        self.brand_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_avatar.setPixmap(QPixmap(str(BRAND_LOGO)).scaled(
            104, 104, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        nav.addWidget(self.brand_avatar)
        nav.addWidget(label(PRIMARY_PROVIDER_LABEL, 'brand'))
        nav.addWidget(label('Không gian sáng tạo', 'muted'))
        nav.addSpacing(28)
        self.nav_buttons = []
        titles = ['Studio giọng nói', 'Hàng đợi TXT', 'SRT', 'Lịch sử', 'Cài đặt']
        for index, title in enumerate(titles):
            item = button(title, lambda i=index: self.navigate(i), 'nav')
            item.setCheckable(True)
            nav.addWidget(item)
            self.nav_buttons.append(item)
        nav.addStretch()
        self.balance_chip = button('Bản quyền', lambda: self.navigate(PAGE_SETTINGS))
        nav.addWidget(self.balance_chip)
        self.theme_button = button('Chế độ tối' if self.theme_mode == 'light' else 'Chế độ sáng', self.toggle_theme)
        nav.addWidget(self.theme_button)
        nav.addWidget(label('DESKTOP STUDIO  /  QT6', 'eyebrow'))
        base.addWidget(sidebar)
        content = QWidget()
        content.setObjectName('workspace')
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 24, 20, 16)
        layout.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(5)
        heading.addWidget(label('KHÔNG GIAN LÀM VIỆC', 'eyebrow'))
        self.page_title = label(titles[0], 'title')
        heading.addWidget(self.page_title)
        self.page_subtitle = label('Từ câu chữ đến âm thanh. Tự nhiên trong từng nhịp đọc.', 'muted')
        self.page_subtitle.setWordWrap(True)
        heading.addWidget(self.page_subtitle)
        header.addLayout(heading, 1)
        layout.addLayout(header)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._studio_page())
        self.pages.addWidget(self._queue_page())
        self.pages.addWidget(self._srt_page())
        self.pages.addWidget(self._history_page())
        self.pages.addWidget(self._settings_page())
        layout.addWidget(self.pages, 1)
        self.recovery_frame = QFrame()
        recovery = QHBoxLayout(self.recovery_frame)
        recovery.setContentsMargins(0, 0, 0, 0)
        self.recovery_label = label('Tác vụ chưa hoàn tất', 'muted')
        recovery.addWidget(self.recovery_label, 1)
        self.resume_button = button('Tiếp tục tác vụ', self.resume_generation)
        recovery.addWidget(self.resume_button)
        layout.addWidget(self.recovery_frame)
        transport, bar = card()
        transport.setObjectName('transport')
        bar.setContentsMargins(18, 13, 18, 13)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        bar.addWidget(self.progress)
        work = QHBoxLayout()
        status_box = QVBoxLayout()
        self.status = label('Sẵn sàng tạo điều mới.', 'muted')
        self.status.setWordWrap(True)
        self.playback_label = label('Âm thanh của bạn sẽ xuất hiện tại đây')
        self.playback_label.setMaximumWidth(520)
        status_box.addWidget(self.status)
        status_box.addWidget(self.playback_label)
        work.addLayout(status_box, 1)
        self.stop_button = button('Dừng', self.stop_generation, 'danger')
        self.stop_button.setFixedWidth(85)
        self.stop_button.setEnabled(False)
        self.generate_button = button('Tạo giọng nói  →', self.start_generation, 'primary')
        self.generate_button.setMaximumWidth(230)
        work.addWidget(self.stop_button)
        work.addWidget(self.generate_button)
        bar.addLayout(work)
        play = QHBoxLayout()
        self.play_button = button('Nghe', self.play_pause)
        self.return_result_button = button('Nghe kết quả', self.return_to_result)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1)
        self.seek.sliderReleased.connect(lambda: self.player.seek(self.seek.value()))
        self.seek.setAccessibleName('Vị trí phát âm thanh')
        self.seek.actionTriggered.connect(lambda action: QTimer.singleShot(0, lambda: self.player.seek(self.seek.value())))
        self.play_time = label('00:00 / 00:00', 'muted')
        self.open_button = button('Mở vị trí', self.open_output)
        self.external_button = button('Mở audio', self.open_external)
        self.play_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.return_result_button.hide()
        self.external_button.hide()
        self.seek.setEnabled(False)
        for widget in (self.play_button, self.return_result_button, self.seek, self.play_time,
                       self.open_button, self.external_button):
            play.addWidget(widget, 1 if widget is self.seek else 0)
        bar.addLayout(play)
        layout.addWidget(transport)
        base.addWidget(content, 1)
        self.nav_buttons[0].setChecked(True)

    def _studio_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(16)
        editor_card, editor_layout = card()
        top = QHBoxLayout()
        top.addWidget(label('Nội dung', 'section'), 1)
        import_button = button('Nhập file', self.import_text)
        import_button.setToolTip('Nhập TXT hoặc SRT UTF-8 · Ctrl+O')
        clear = button('Xóa', lambda: self.editor.clear())
        self.clear_text_button = clear
        top.addWidget(import_button)
        top.addWidget(clear)
        editor_layout.addLayout(top)
        # Kept as a hidden compatibility state for older drafts/tests; navigation is now authoritative.
        self.input_mode = QComboBox(self)
        self.input_mode.addItem('Văn bản', 'text')
        self.input_mode.addItem('SRT theo timeline', 'srt')
        self.input_mode.hide()
        self.editor = QPlainTextEdit()
        self.editor.setObjectName('editor')
        self.editor.setAccessibleName('Nội dung cần chuyển thành giọng nói')
        self.editor.setPlaceholderText('Viết hoặc dán nội dung tại đây…\n\nMột ý tưởng nhỏ. Một câu chuyện lớn.\nHãy để giọng nói mang nội dung của bạn đi xa hơn.')
        self.editor.textChanged.connect(self.schedule_save)
        self.input_mode.currentIndexChanged.connect(self.input_mode_changed)
        editor_layout.addWidget(self.editor, 1)
        self.text_error = label('', 'fieldError')
        self.text_error.setWordWrap(True)
        self.text_error.hide()
        editor_layout.addWidget(self.text_error)
        self.editor.textChanged.connect(lambda: self.clear_field_error(self.editor, self.text_error))
        footer = QHBoxLayout()
        self.stats = label('0 ký tự  ·  0 phân đoạn', 'muted')
        self.stats.setWordWrap(True)
        footer.addWidget(self.stats, 1)
        shortcut_hint = label('Ctrl + Enter để tạo', 'muted')
        shortcut_hint.setWordWrap(True)
        footer.addWidget(shortcut_hint)
        editor_layout.addLayout(footer)
        file_row = QHBoxLayout()
        self.filename = QLineEdit()
        self.filename.setPlaceholderText('Tên file — tự động nếu để trống')
        self.filename.textChanged.connect(self.schedule_save)
        filename_label = label('Tên file', 'muted')
        filename_label.setBuddy(self.filename)
        file_row.addWidget(filename_label)
        file_row.addWidget(self.filename)
        editor_layout.addLayout(file_row)
        self.editor_scroll = scroll(editor_card)
        self.editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(self.editor_scroll)
        self.controls += [import_button, clear, self.filename]
        settings_card, settings = card()
        settings_header = QHBoxLayout()
        settings_title = label('Thiết lập giọng đọc', 'section')
        settings_title.setWordWrap(True)
        settings_header.addWidget(settings_title, 1)
        popular = button('★  Giọng\nphổ biến', self.open_voice_picker)
        popular.setToolTip('Xem các giọng được dùng nhiều nhất')
        popular.setAccessibleName('Xem các giọng được dùng nhiều nhất')
        popular.setCursor(Qt.CursorShape.PointingHandCursor)
        popular.setStyleSheet(
            'QPushButton {background:#ffd166;color:#241a0b;border:1px solid #ffda80;'
            'border-radius:8px;padding:6px 9px;font-weight:700;}'
            'QPushButton:hover {background:#ffe6a3;}'
            'QPushButton:disabled {background:#615746;color:#d8ccb4;}')
        settings_header.addWidget(popular)
        settings.addLayout(settings_header)
        self.controls.append(popular)
        settings.setSpacing(10)
        settings.setContentsMargins(16, 16, 16, 16)
        settings_card.setMaximumWidth(420)
        self.provider = QComboBox()
        for value, text in PROVIDER_LABELS:
            self.provider.addItem(text, value)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        settings.addWidget(label('DỊCH VỤ', 'eyebrow'))
        settings.addWidget(self.provider)
        settings.addWidget(label('GIỌNG ĐỌC', 'eyebrow'))
        self.search = QLineEdit()
        self.search.setAccessibleName('Tìm giọng đọc')
        self.search.setPlaceholderText('Tìm giọng đọc…')
        self.search.textChanged.connect(self.filter_voices)
        settings.addWidget(self.search)
        filter_row = QHBoxLayout()
        self.language = QComboBox()
        self.region = QComboBox()
        self.language.setAccessibleName('Lọc ngôn ngữ')
        self.region.setAccessibleName('Lọc vùng giọng')
        self.language.currentIndexChanged.connect(self.filter_voices)
        self.region.currentIndexChanged.connect(self.filter_voices)
        filter_row.addWidget(self.language, 1)
        filter_row.addWidget(self.region, 1)
        settings.addLayout(filter_row)
        self.voice = QComboBox()
        self.voice.setAccessibleName('Giọng đọc được chọn')
        self.voice.setMaxVisibleItems(8)
        self.voice.currentIndexChanged.connect(self.schedule_save)
        self.voice.currentIndexChanged.connect(self.refresh_favorite_button)
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.voice, 1)
        self.favorite_button = button('☆', self.toggle_favorite_voice)
        self.favorite_button.setCheckable(True)
        self.favorite_button.setFixedSize(38, 36)
        voice_row.addWidget(self.favorite_button)
        settings.addLayout(voice_row)
        self.preview_button = button('Nghe thử giọng', self.preview)
        settings.addWidget(self.preview_button)
        self.preview_hint = label('Mẫu gốc có sẵn · miễn phí', 'muted')
        self.preview_hint.setWordWrap(True)
        settings.addWidget(self.preview_hint)
        form = QFormLayout()
        form.setSpacing(12)
        self.model = QComboBox()
        self.model.currentIndexChanged.connect(self.model_changed)
        self.speed = QDoubleSpinBox()
        self.pitch = QDoubleSpinBox()
        for spin in (self.speed, self.pitch):
            spin.setRange(.5, 2)
            spin.setSingleStep(.1)
            spin.setDecimals(1)
            spin.setValue(1)
            spin.setSuffix(' ×')
            spin.valueChanged.connect(self.schedule_save)
        self.bitrate = QComboBox()
        self.bitrate.addItems(['128 kbps', '192 kbps', '256 kbps'])
        self.bitrate.currentIndexChanged.connect(self.schedule_save)
        self.workers = QSpinBox()
        self.workers.setRange(1, 8)
        self.workers.setValue(self.store.settings['concurrency'])
        self.workers.valueChanged.connect(self.schedule_save)
        for title, widget in [('Mô hình', self.model), ('Tốc độ', self.speed), ('Luồng xử lý', self.workers)]:
            form.addRow(title, widget)
        settings.addLayout(form)
        settings.addWidget(label('THƯ MỤC XUẤT', 'eyebrow'))
        output_row = QHBoxLayout()
        self.output_dir = QLineEdit(self.store.settings['output_dir'])
        self.output_dir.textChanged.connect(self.schedule_save)
        choose = button('…', self.browse_output)
        choose.setAccessibleName('Chọn thư mục xuất âm thanh')
        choose.setToolTip('Chọn thư mục xuất âm thanh')
        self.output_dir.setAccessibleName('Thư mục xuất âm thanh')
        choose.setFixedWidth(38)
        output_row.addWidget(self.output_dir, 1)
        output_row.addWidget(choose)
        settings.addLayout(output_row)
        self.advanced_button = button('Tùy chọn nâng cao  +')
        self.advanced_button.setCheckable(True)
        settings.addWidget(self.advanced_button)
        self.advanced_panel = QWidget()
        advanced_form = QFormLayout(self.advanced_panel)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(12)
        advanced_form.addRow('Cao độ', self.pitch)
        advanced_form.addRow('Chất lượng', self.bitrate)
        settings.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        self.advanced_button.toggled.connect(self.advanced_panel.setVisible)
        self.advanced_button.toggled.connect(lambda expanded: self.advanced_button.setText(
            'Tùy chọn nâng cao  −' if expanded else 'Tùy chọn nâng cao  +'))
        settings.addStretch()
        settings_scroll = scroll(settings_card)
        settings_scroll.setMinimumWidth(332)
        splitter.addWidget(settings_scroll)
        splitter.setSizes([650, 330])
        layout.addWidget(splitter)
        self.controls += [self.provider, self.search, self.language,
                          self.region, self.voice, self.preview_button, self.model, self.speed,
                          self.pitch, self.bitrate, self.workers, self.output_dir, choose]
        return page

    def _srt_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        options, option_layout = card('SRT theo timeline')
        row = QHBoxLayout()
        row.addWidget(label('Số luồng SRT', 'muted'))
        self.srt_workers = QSpinBox()
        self.srt_workers.setRange(1, 8)
        self.srt_workers.setValue(int(self.store.settings.get('srt_workers', 5)))
        self.srt_workers.valueChanged.connect(self.schedule_save)
        row.addWidget(self.srt_workers)
        self.srt_stats = label('Nhập file .srt hoặc mở project SRT', 'muted')
        self.srt_stats.setWordWrap(True)
        row.addWidget(self.srt_stats, 1)
        option_layout.addLayout(row)
        layout.addWidget(options)
        self.srt_panel = SrtPanel(self)
        self.srt_panel.changed.connect(self.srt_changed)
        self.srt_panel.draft_changed.connect(self.schedule_save)
        self.srt_panel.project_loaded.connect(self.srt_project_loaded)
        self.srt_panel.preview_requested.connect(self.preview_srt_cue)
        self.srt_panel.regenerate_requested.connect(self.regenerate_srt_cue)
        self.srt_panel.plain_text_requested.connect(self.srt_to_text)
        layout.addWidget(self.srt_panel, 1)
        self.controls.append(self.srt_workers)
        return page

    def _queue_page(self):
        page, layout = card('Một danh sách. Nhiều câu chuyện.')
        help_text = label('Mỗi TXT tạo một MP3. Các file chạy lần lượt, mỗi file dùng số luồng đã chọn.', 'muted')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        actions = QHBoxLayout()
        self.queue_buttons = []
        for title, callback in [('＋ Thêm TXT', self.queue_import), ('↑', lambda: self.queue_move(-1)),
                                ('↓', lambda: self.queue_move(1)), ('Sắp tên', self.queue_sort),
                                ('Bỏ file', self.queue_remove), ('Bỏ qua lỗi', self.queue_skip)]:
            item = button(title, callback)
            self.queue_buttons.append(item)
            actions.addWidget(item)
        actions.addStretch()
        layout.addLayout(actions)
        self.queue_table = table(['#', 'Tên file', 'Ký tự', 'Trạng thái'])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.queue_table, 1)
        self.queue_summary = label('Chọn giọng và thông số ở Studio, rồi bắt đầu hàng đợi.', 'muted')
        self.queue_summary.setWordWrap(True)
        layout.addWidget(self.queue_summary)
        return page

    def _proxy_page(self):
        page, layout = card('Kết nối theo cách của bạn')
        hint = label('Thêm, kiểm tra và kích hoạt proxy tùy chỉnh cho Voice Pro.', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QGridLayout()
        self.proxy_buttons = []
        for index, (title, fn) in enumerate([('＋ Thêm proxy', self.edit_proxy), ('Chỉnh sửa', lambda: self.edit_proxy(True)),
                          ('Kích hoạt', self.activate_proxy), ('Xóa', self.delete_proxy),
                          ('Nhập TXT', self.import_proxies), ('Kiểm tra', lambda: self.proxy_action(False)),
                          ('Đổi IP', lambda: self.proxy_action(True))]):
            item = button(title, fn)
            self.proxy_buttons.append(item)
            row.addWidget(item, index // 4, index % 4)
        layout.addLayout(row)
        self.proxy_table = table(['Tên', 'Tuyến', 'Sử dụng', 'IP', 'Trạng thái', 'Độ trễ'])
        self.proxy_table.doubleClicked.connect(lambda: self.edit_proxy(True))
        layout.addWidget(self.proxy_table, 1)
        layout.addWidget(label('Lưu lượng đã ghi nhận', 'section'))
        self.traffic_table = table(['Proxy', 'Gửi', 'Nhận', 'Request', 'Phương pháp'])
        self.traffic_table.setMaximumHeight(175)
        layout.addWidget(self.traffic_table)
        traffic_actions = QHBoxLayout()
        traffic_actions.addWidget(label('Không thay thế hóa đơn proxy. Lưu lượng CapCut không được ghi tại đây.', 'muted'), 1)
        traffic_actions.addWidget(button('Xuất CSV', self.export_traffic))
        self.reset_traffic_button = button('Đặt lại', self.reset_traffic)
        traffic_actions.addWidget(self.reset_traffic_button)
        layout.addLayout(traffic_actions)
        return page

    def _history_page(self):
        page, layout = card('Những âm thanh đã tạo')
        layout.addWidget(label('10 kết quả gần nhất. Bỏ khỏi danh sách vẫn giữ nguyên file audio.', 'muted'))
        self.history_table = table(['Tên file', 'Thời gian', 'Vị trí'])
        self.history_table.doubleClicked.connect(self.play_history)
        layout.addWidget(self.history_table, 1)
        actions = QHBoxLayout()
        actions.addWidget(button('▷  Nghe kết quả', self.play_history))
        actions.addWidget(button('Mở thư mục', self.open_history))
        actions.addWidget(button('Bỏ khỏi lịch sử', self.remove_history))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(label('Hoạt động', 'section'))
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(100)
        self.activity.setMaximumHeight(150)
        layout.addWidget(self.activity)
        return page

    def _settings_page(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        appearance, app_layout = card('Giao diện & trải nghiệm')
        theme_row = QHBoxLayout()
        theme_row.addWidget(label('Giao diện sáng thoáng hoặc tối dịu mắt. Lựa chọn được ghi nhớ.', 'muted'), 1)
        theme_row.addWidget(button('Chuyển sáng / tối', self.toggle_theme))
        app_layout.addLayout(theme_row)
        layout.addWidget(appearance)
        connection, net = card('Kết nối Voice Pro')
        self.random_device = QCheckBox('Thay đổi hồ sơ thiết bị cho từng phân đoạn Voice Pro')
        self.random_device.setChecked(self.store.settings['randomize_device'])
        # Preserve the internal routing preference without exposing proxy controls to customers.
        self.direct_download = QCheckBox()
        self.direct_download.setChecked(self.store.settings['download_via_direct_ip'])
        self.session_id = QLineEdit()
        self.session_id.setEchoMode(QLineEdit.EchoMode.Password)
        self.session_id.setPlaceholderText('Voice Pro sessionid tùy chọn · chỉ RAM')
        for item in (self.random_device, self.session_id):
            net.addWidget(item)
            self.controls.append(item)
        self.random_device.toggled.connect(self.schedule_save)
        layout.addWidget(connection)
        saydi_card, saydi_settings = card('Kết nối Saydi')
        self.saydi_token = QLineEdit()
        self.saydi_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.saydi_token.setPlaceholderText('Để trống để tự tạo phiên Saydi')
        self.saydi_token.setAccessibleName('Access token Saydi')
        saydi_settings.addWidget(self.saydi_token)
        self.controls.append(self.saydi_token)
        layout.addWidget(saydi_card)
        license_card, lic = card('Bản quyền & hạn mức')
        self.balance_detail = label('Đang kiểm tra…', 'muted')
        self.balance_detail.setWordWrap(True)
        lic.addWidget(self.balance_detail)
        machine_row = QHBoxLayout()
        self.machine = QLineEdit(self.license_manager.machine if self.license_manager else self.license_error)
        self.machine.setReadOnly(True)
        machine_row.addWidget(self.machine, 1)
        machine_row.addWidget(button('Sao chép mã máy', lambda: QApplication.clipboard().setText(self.machine.text())))
        lic.addLayout(machine_row)
        key_row = QHBoxLayout()
        self.license_key = QLineEdit()
        self.license_key.setPlaceholderText('Dán toàn bộ key EVKEY1-…')
        self.license_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.license_key.returnPressed.connect(self.activate_license)
        self.activate_license_button = button('Kích hoạt', self.activate_license, 'primary')
        key_row.addWidget(self.license_key, 1)
        key_row.addWidget(self.activate_license_button)
        lic.addLayout(key_row)
        more = QHBoxLayout()
        self.import_license_button = button('Nhập file license', self.import_license)
        self.backup_wallet_button = button('Sao lưu ví', self.backup_wallet)
        more.addWidget(self.import_license_button)
        more.addWidget(self.backup_wallet_button)
        more.addStretch()
        lic.addLayout(more)
        layout.addWidget(license_card)
        support, sup = card('Dữ liệu & hỗ trợ')
        root_label = label('Dữ liệu: ' + str(self.store.root), 'muted')
        root_label.setWordWrap(True)
        sup.addWidget(root_label)
        support_row = QGridLayout()
        for index, (title, callback) in enumerate([('Mở dữ liệu', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))),
                                ('Sao chép hỗ trợ', self.copy_support), ('Nhật ký quản trị', self.open_admin),
                                ('Đặt mật khẩu log', self.set_admin_password), ('Giấy phép', self.show_notices)]):
            support_row.addWidget(button(title, callback), index // 3, index % 3)
        sup.addLayout(support_row)
        layout.addWidget(support)
        layout.addStretch()
        return scroll(content)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.input_mode.blockSignals(True)
        self.input_mode.setCurrentIndex(1 if index == PAGE_SRT else 0)
        self.input_mode.blockSignals(False)
        for i, item in enumerate(self.nav_buttons):
            item.setChecked(i == index)
        self.page_title.setText(self.nav_buttons[index].text())
        subtitles = ['Từ câu chữ đến âm thanh. Tự nhiên trong từng nhịp đọc.',
                     'Tổ chức nội dung và tạo nhiều file âm thanh trong một lượt.',
                     'Biên tập cue, tạo giọng và xuất âm thanh đúng timeline.',
                     'Tìm lại, nghe lại và tiếp tục sáng tạo.',
                     'Một không gian làm việc theo cách của bạn.']
        self.page_subtitle.setText(subtitles[index])
        self.generate_button.setText('Chạy hàng đợi  →' if index == PAGE_QUEUE else
            'Tạo / xuất SRT  →' if index == PAGE_SRT else 'Tạo giọng nói  →')
        self.update_stats()
        self.refresh_recovery()

    def toggle_theme(self):
        self.theme_mode = 'dark' if self.theme_mode == 'light' else 'light'
        apply_theme(QApplication.instance(), self.theme_mode)
        self.theme_button.setText('Chế độ tối' if self.theme_mode == 'light' else 'Chế độ sáng')
        self.apply_icons()
        try:
            atomic_json(self.store.root / 'qt-ui.json', dict(theme=self.theme_mode))
        except OSError:
            self.error('Không lưu được giao diện. Kiểm tra quyền ghi thư mục dữ liệu.')

    def apply_icons(self):
        self.qt_icons = {name: make_icon(name, self.theme_mode) for name in
                         ('wave', 'play', 'pause', 'stop', 'sun', 'moon', 'queue', 'history', 'settings')}
        for widget, name in ((self.preview_button, 'play'), (self.play_button, 'play'),
                             (self.stop_button, 'stop'), (self.theme_button, 'moon' if self.theme_mode == 'light' else 'sun')):
            widget.setIcon(self.qt_icons[name])
        self.generate_button.setIcon(make_icon('wave', self.theme_mode,
                                    '#ffffff' if self.theme_mode == 'light' else '#101521'))
        for widget, name in zip(self.nav_buttons, ('wave', 'queue', 'wave', 'history', 'settings')):
            widget.setIcon(self.qt_icons[name])

    def provider_id(self):
        return self.provider.currentData() or 'everai'

    def provider_changed(self):
        if not hasattr(self, 'model'):
            return
        provider = self.provider_id()
        self.preview_hint.setText('Mẫu gốc có sẵn · miễn phí' if provider == 'everai'
                                 else 'Render mẫu mới sử dụng hạn mức; có cache phát lại.')
        self.model.blockSignals(True)
        self.model.clear()
        if provider == 'everai':
            for version in ('v1.6', 'v1.5'):
                self.model.addItem(PRIMARY_MODEL_PREFIX + version, 'everai-' + version)
        else:
            value = dict(PROVIDER_LABELS)[provider]
            self.model.addItem(value, value)
        self.model.blockSignals(False)
        self.model.setEnabled(not self.busy and provider == 'everai')
        self.pitch.setEnabled(not self.busy and provider == 'everai')
        self.bitrate.setEnabled(not self.busy and provider == 'everai')
        if provider != 'everai':
            self.pitch.setValue(1)
            self.bitrate.setCurrentIndex(0)
        self.region.blockSignals(True)
        self.region.clear()
        self.region.addItem('Mọi vùng', None)
        for value in sorted({v['region'] for v in STUDIO_VOICES if v['engine'] == provider and v['region']}):
            self.region.addItem(value, value)
        self.region.blockSignals(False)
        self.refresh_languages()
        self.filter_voices()
        self.schedule_save()

    def refresh_languages(self):
        provider = self.provider_id()
        previous = self.language.currentData()
        codes = {v['lang'] for v in STUDIO_VOICES if v['engine'] == provider}
        self.language.blockSignals(True)
        self.language.clear()
        self.language.addItem('Mọi ngôn ngữ', None)
        for code in sorted(codes, key=lambda code: (code != 'vi', language_label(code).casefold())):
            self.language.addItem(language_label(code), code)
        self.language.setCurrentIndex(max(0, self.language.findData(previous)))
        self.language.blockSignals(False)

    def model_changed(self):
        if hasattr(self, 'language'):
            self.refresh_languages()
            self.filter_voices()

    def filter_voices(self):
        if not hasattr(self, 'voice'):
            return
        previous = self.voice.currentData()
        provider = self.provider_id()
        query = self.search.text().casefold().strip()
        language = self.language.currentData()
        region = self.region.currentData()
        self.voice.blockSignals(True)
        self.voice.clear()
        for item in sorted(STUDIO_VOICES, key=lambda item: (
                item['id'] not in self.favorite_voices, item['lang'] != 'vi', item['lang'],
                item['region'].casefold(), item['name'].casefold())):
            if (item['engine'] == provider and (not region or item['region'] == region)
                    and (not language or item['lang'] == language)
                    and query in (item['name'] + ' ' + item['id'] + ' ' + item['region']).casefold()):
                self.voice.addItem(('★ ' if item['id'] in self.favorite_voices else '') + item['name'] + (' · ' + item['region'] if item['region'] else ''), item['id'])
                self.voice.setItemData(self.voice.count()-1, item['id'], Qt.ItemDataRole.ToolTipRole)
        self.voice.setCurrentIndex(max(0, self.voice.findData(previous)))
        self.voice.blockSignals(False)
        self.refresh_favorite_button()
        self.schedule_save()

    def refresh_favorite_button(self, *args):
        voice_id = self.voice.currentData()
        favorite = voice_id in self.favorite_voices
        self.favorite_button.setChecked(favorite)
        self.favorite_button.setText('★' if favorite else '☆')
        title = 'Bỏ khỏi giọng yêu thích' if favorite else 'Thêm vào giọng yêu thích'
        self.favorite_button.setToolTip(title)
        self.favorite_button.setAccessibleName(title)
        self.favorite_button.setEnabled(bool(voice_id))

    def toggle_favorite_voice(self):
        voice_id = self.voice.currentData()
        if voice_id:
            self.toggle_favorite_by_id(voice_id)

    def toggle_favorite_by_id(self, voice_id):
        favorites = self.favorite_voices ^ {voice_id}
        try:
            atomic_json(self.store.root / 'favorite-voices.json', sorted(favorites))
        except (OSError, ValueError):
            self.refresh_favorite_button()
            self.error('Không lưu được giọng yêu thích. Kiểm tra thư mục dữ liệu.')
            return False
        self.favorite_voices = favorites
        self.filter_voices()
        return True

    def snapshot(self):
        speaker = self.voice.currentData()
        if not speaker:
            raise ValueError('Hãy chọn một giọng đọc.')
        model = self.model.currentData()
        return dict(speaker_id=speaker, model_id=model, speed=round(self.speed.value(), 1),
                    pitch=round(self.pitch.value(), 1), volume=100,
                    bitrate=self.bitrate.currentText(), audio_type='mp3')

    def _restore(self):
        settings = self.store.settings
        provider, _ = split_voice_id(settings['voice_id'])
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.provider_changed()
        self.voice.setCurrentIndex(max(0, self.voice.findData(settings['voice_id'])))
        model, _, language = settings['model_id'].partition('@')
        index = self.model.findData(model)
        if index >= 0:
            self.model.setCurrentIndex(index)
        if language:
            self.language.setCurrentIndex(max(0, self.language.findData(language)))
        elif settings.get('tts_language'):
            index = self.language.findText(settings['tts_language'])
            if index >= 0:
                self.language.setCurrentIndex(index)
        self.speed.setValue(settings['speed'])
        self.pitch.setValue(settings['pitch'] if provider == 'everai' else 1)
        self.bitrate.setCurrentText(settings['bitrate'])
        draft = self.store.load('draft.json', {})
        if isinstance(draft, dict):
            self.editor.setPlainText(str(draft.get('text', '')))
            self.filename.setText(str(draft.get('filename', '')))
            self.source_name = str(draft.get('source', ''))
        self.update_stats()

    def schedule_save(self, *args):
        if self.restoring:
            return
        if hasattr(self, 'save_timer'):
            self.save_timer.start()

    def save_state(self):
        if self.restoring:
            return True
        try:
            settings = self.store.settings
            if self.voice.currentData():
                config = self.snapshot()
                settings.update(voice_id=config['speaker_id'], model_id=config['model_id'],
                                speed=config['speed'], pitch=config['pitch'], bitrate=config['bitrate'])
            settings.update(output_dir=self.output_dir.text(), concurrency=self.workers.value(),
                            srt_workers=self.srt_workers.value(),
                            randomize_device=self.random_device.isChecked(),
                            download_via_direct_ip=self.direct_download.isChecked(), use_proxy=True,
                            tts_provider=self.provider_id(),
                            tts_language=self.language.currentText() if self.language.currentData() else 'Tất cả')
            self.store.save_settings()
            atomic_json(self.store.root / 'draft.json', dict(text=self.editor.toPlainText(),
                        filename=self.filename.text(), source=self.source_name))
            atomic_json(self.store.root / 'srt-draft.json', dict(
                mode='srt' if self.pages.currentIndex() == PAGE_SRT else 'text',
                project=str(self.srt_panel.project.root) if self.srt_panel.project else None,
                policy=self.srt_panel.policy.currentData(), max_tempo=self.srt_panel.tempo.value(),
                format=self.srt_panel.output_format.currentData(), cue_drafts=self.srt_panel.drafts))
            return True
        except (OSError, ValueError):
            self.error('Không lưu được bản nháp/cấu hình. Kiểm tra thư mục dữ liệu.')
            return False

    def update_stats(self):
        if not hasattr(self, 'stats'):
            return
        if self.pages.currentIndex() == PAGE_SRT:
            project = self.srt_panel.project
            if project:
                provider = self.provider_id()
                chunks = [chunk for cue in project.data['cues'] for chunk in srt_chunks_for(cue['text'], provider)]
                self.srt_stats.setText(f'{len(project.data["cues"]):,} cue · {len(chunks):,} phân đoạn · {sum(map(len, chunks)):,} ký tự lời thoại')
            else:
                self.srt_stats.setText('Nhập file .srt hoặc mở project SRT')
            return
        text = self.editor.toPlainText()
        try:
            chunks = speech_chunks(text, self.provider_id()) if text.strip() else []
            self.stats.setText(f'{sum(map(len, chunks)):,} ký tự  ·  {len(chunks)} phân đoạn')
        except ValueError:
            self.stats.setText('SRT chưa hợp lệ · kiểm tra mốc thời gian')

    def error(self, message, code='local'):
        self.last_error_code = code
        self.status.setText(message)
        self.status.setToolTip(message)

    def clear_field_error(self, field, message):
        if field.property('invalid'):
            field.setProperty('invalid', False)
            field.style().unpolish(field)
            field.style().polish(field)
            field.setAccessibleDescription('')
            message.hide()

    def field_error(self, field, message, text):
        field.setProperty('invalid', True)
        field.style().unpolish(field)
        field.style().polish(field)
        field.setAccessibleDescription(text)
        message.setText(text)
        message.show()
        self.navigate(0)
        field.setFocus()
        parent = field.parentWidget()
        while parent and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent:
            parent.ensureWidgetVisible(field)

    def log(self, message, level='INFO'):
        message = redact_log(message)
        self.logger.log(getattr(logging, level, logging.INFO), message)
        self.ui_queue.put(('log', (time.strftime('%H:%M:%S'), level, message)))

    def new_engine(self, provider=None):
        if self.license_manager is None:
            raise LicenseError(self.license_error)
        if self.network_worker:
            raise ValueError('Chờ kiểm tra / đổi IP hoàn tất trước khi tạo giọng.')
        if not ensure_ffmpeg():
            raise ValueError('Thiếu FFmpeg. Hãy dùng bản phát hành đầy đủ.')
        if not self.save_state():
            raise OSError('Không lưu được cấu hình.')
        provider = provider or self.provider_id()
        engine_type = EverAITTSEngine
        options = {}
        if provider != 'everai':
            engine_type = MultiProviderTTSEngine
            options['saydi_token'] = self.saydi_token.text().strip()
            TTSService(**options).preflight(provider)
        catalog = self.proxy_catalog if provider == 'everai' else None
        return engine_type(proxy_catalog=catalog, session_id=self.session_id.text().strip() or None,
            license_manager=self.license_manager, concurrency=self.workers.value(),
            randomize_device=self.random_device.isChecked(),
            download_via_direct_ip=self.direct_download.isChecked(),
            temp_dir=self.store.root / 'samples/chunks', log_callback=self.log,
            progress_callback=lambda c, t, p, text: self.ui_queue.put(('progress', p)),
            status_callback=lambda kind, details: self.ui_queue.put(('status', (kind, details))), **options)

    def start_generation(self):
        if self.busy or self.closing:
            return
        if self.pages.currentIndex() == PAGE_QUEUE:
            self.queue_start()
            return
        if self.pages.currentIndex() == PAGE_SRT:
            self.start_srt_generation()
            return
        try:
            text = self.editor.toPlainText().strip()
            if not text:
                self.field_error(self.editor, self.text_error, 'Nhập nội dung để bắt đầu.')
                raise ValueError('Nhập nội dung để bắt đầu.')
            config = self.snapshot()
            output = writable_directory(Path(self.output_dir.text()))
            name = audio_filename(self.filename.text() or self.source_name or ' '.join(text.split()[:8]))
            engine = self.new_engine()
            job = self.store.root / 'jobs' / uuid.uuid4().hex
            self.run_job(engine, text, output / name, config, job)
        except (ValueError, OSError) as error:
            self.error(str(error) if isinstance(error, ValueError) else 'Không ghi được nơi lưu. Chọn thư mục khác.', 'preflight')

    def run_job(self, engine, text, destination, config, job, preview=False):
        if self.worker or self.busy:
            raise ValueError('Chờ worker hiện tại kết thúc.')
        self.engine = engine
        self.active_job = Path(job)
        self.preview_running = preview
        if not preview:
            self.pending_job = Path(job)
        self.progress.setValue(0)
        self.status.setText('Đang tạo mẫu nghe thử…' if preview else 'Đang chuẩn bị tác vụ…')
        self.proxy_catalog.suspended = True
        self.set_busy(True)
        worker = TaskThread(lambda: engine.process(text, destination, **config, job_dir=job,
                                                   keep_temp_chunks=not preview), self)
        self.worker = worker
        worker.finished.connect(self.finish_job)
        worker.start()

    def set_busy(self, busy):
        self.busy = busy
        for item in self.controls + self.queue_buttons:
            item.setEnabled(not busy and not self.network_worker)
        self.editor.setReadOnly(busy)
        self.srt_panel.set_busy(busy or bool(self.network_worker))
        self.generate_button.setEnabled(not busy and not self.network_worker)
        self.stop_button.setEnabled(busy)
        self.resume_button.setEnabled(not busy)
        for widget in (self.license_key, self.activate_license_button,
                       self.import_license_button, self.backup_wallet_button):
            widget.setEnabled(not busy and self.license_manager is not None)
        if not busy:
            provider = self.provider_id()
            self.pitch.setEnabled(provider == 'everai')
            self.bitrate.setEnabled(provider == 'everai')
            self.model.setEnabled(provider == 'everai')

    def finish_job(self):
        worker = self.worker
        if worker is None:
            return
        if worker.isRunning():
            QTimer.singleShot(20, self.finish_job)
            return
        result, error = worker.result, worker.error
        self.worker = None
        worker.deleteLater()
        engine = self.engine
        self.engine = None
        preview = self.preview_running
        self.preview_running = False
        self.proxy_catalog.suspended = False
        self.set_busy(False)
        if engine:
            try:
                for entry in engine.get_proxy_traffic_stats():
                    self.store.record_proxy_traffic(entry.get('proxy_id'), entry.get('name'), entry.get('address'),
                        dict(proxy_tx_bytes=entry.get('tx_bytes', 0), proxy_rx_bytes=entry.get('rx_bytes', 0),
                             proxy_request_count=entry.get('request_count', 0), measurement=entry.get('measurement', 'unknown')))
            except (OSError, TypeError, ValueError):
                self.log('Không lưu được bộ đếm lưu lượng proxy.', 'WARNING')
        if error:
            self.log('Tác vụ kết thúc: ' + type(error).__name__, 'WARNING')
            # Preserve the existing provider-error presentation contract.
            if isinstance(error, EverAICancelled):
                message = 'Đã dừng. Các đoạn đã hoàn thành được giữ lại.'
            elif isinstance(error, LicenseError):
                message = str(error)
            elif isinstance(error, OSError):
                message = 'Không lưu được file. Chọn nơi lưu khác rồi tiếp tục.'
            elif isinstance(error, AudioAssemblyError):
                message = 'Không ghép được âm thanh. Chọn nơi lưu khác hoặc dùng bản ứng dụng có FFmpeg đầy đủ.'
            elif isinstance(error, EverAIQuotaError):
                message = 'Dịch vụ đang giới hạn chuyển đổi. Kiểm tra tài khoản hoặc thử lại sau.'
            else:
                message = 'Chưa tạo xong âm thanh. Phần đã tạo được giữ lại; kiểm tra kết nối rồi thử lại.'
            self.error(message, 'generation')
        elif preview:
            self.load_playback(result, 'Mẫu nghe thử')
            self.play_pause()
            self.status.setText('Mẫu nghe thử đã sẵn sàng.')
        else:
            self.last_output_file = Path(result)
            try:
                self.store.add_result(result)
            except OSError:
                self.log('Không lưu được lịch sử kết quả.', 'WARNING')
            self.load_playback(result)
            self.progress.setValue(1000)
            self.status.setText('Đã lưu ' + Path(result).name)
            self.refresh_history()
        if self.queue_current is not None and not preview:
            self.queue_current['state'] = 'error' if error else 'done'
            if result:
                self.queue_current['result'] = str(result)
            self.queue_current = None
            if error or self.closing:
                self.queue_running = False
            saved = self.refresh_queue()
            if self.queue_running and saved:
                QTimer.singleShot(0, self.queue_next)
        self.refresh_recovery()
        self.refresh_balance()
        self.refresh_proxies()
        if self.closing:
            self.close()

    def _poll(self):
        for _ in range(150):
            try:
                kind, value = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.progress.setValue(max(0, min(1000, int(float(value)*10))))
            elif kind == 'srt':
                phase = value['phase']
                done, total = value.get('completed', 0), value.get('total', 0)
                labels = dict(rendering='Tạo giọng SRT', fitting='Căn thời gian SRT',
                              exporting='Xuất timeline SRT', completed='Đã xuất SRT')
                if self.busy and isinstance(self.engine, SrtRunner):
                    self.status.setText(labels.get(phase, phase) + (f' · {done}/{total} cue' if total else '…'))
                    if phase == 'rendering':
                        self.status.setText(self.status.text() +
                            f' · {value.get("active", 0)}/{value.get("workers", 1)} luồng đang chạy')
                    base, scale = (0, 700) if phase == 'rendering' else (700, 200)
                    self.progress.setValue(1000 if phase == 'completed' else
                                           950 if phase == 'exporting' else base + int(scale * done / max(1, total)))
                if self.srt_panel.project:
                    self.srt_panel.sync_event(value.get('cue_id'))
            elif kind == 'status':
                state, details = value
                messages = dict(preparing='Đang chuẩn bị…', generating='Đang tạo giọng nói…',
                                rendering='Đang tạo giọng nói…', merging='Đang ghép âm thanh…',
                                interrupted='Tác vụ đã tạm dừng.', completed='Đã lưu âm thanh.')
                if self.busy:
                    text = messages.get(state, 'Đang xử lý…')
                    if state == 'generating' and 'total' in details:
                        text = f'Đã tạo {details.get("completed", 0)}/{details["total"]} phân đoạn…'
                    if self.queue_current:
                        text = self.queue_current['name'] + ' · ' + text
                    self.status.setText(text)
            elif kind == 'log':
                self.log_buffer.append(value)
                stamp, level, message = value
                if level != 'DEBUG':
                    # Activity is a concise public summary; full redacted log is admin-only.
                    self.activity.appendPlainText(f'{stamp}  {level}')
        self.update_player()

    def stop_generation(self):
        self.queue_running = False
        if self.engine:
            self.engine.cancel()
            self.stop_button.setEnabled(False)
            self.status.setText('Đang dừng · chờ worker kết thúc…')

    def refresh_recovery(self):
        if not hasattr(self, 'recovery_frame'):
            return
        if self.pages.currentIndex() == PAGE_SRT:
            project = self.srt_panel.project
            pending = project and project.data['state'] != 'completed'
            self.recovery_frame.setVisible(bool(pending))
            if pending:
                self.recovery_label.setText('Project SRT được giữ lại · tiếp tục tạo/căn/xuất phần còn thiếu')
            return
        ignored = {i.get('job') for i in self.txt_queue['items'] if i['state'] in ('done', 'skipped')}
        jobs = [(p, m) for p, m in self.store.unfinished_jobs() if p.name not in ignored]
        remaining = [i for i in self.txt_queue['items'] if i['state'] not in ('done', 'skipped')]
        if self.pages.currentIndex() == PAGE_QUEUE and remaining:
            allowed = {i.get('job') for i in remaining}
            jobs = [(p, m) for p, m in jobs if p.name in allowed]
        self.pending_job = jobs[0][0] if jobs else None
        self.recovery_frame.setVisible(bool(jobs))
        if jobs:
            self.recovery_label.setText(f'Tác vụ chưa hoàn tất · đã giữ {len(jobs[0][1].get("completed", {}))} đoạn')

    def restore_config(self, config):
        provider, _ = split_voice_id(config['speaker_id'])
        self.search.clear()
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        self.provider_changed()
        index = self.voice.findData(config['speaker_id'])
        if index < 0:
            raise ValueError('Giọng của tác vụ cũ không còn trong catalog. Nội dung và job cũ vẫn được giữ.')
        self.voice.setCurrentIndex(index)
        model, _, language = config['model_id'].partition('@')
        index = self.model.findData(model)
        if index >= 0:
            self.model.setCurrentIndex(index)
        if language:
            self.language.setCurrentIndex(max(0, self.language.findData(language)))
        self.speed.setValue(config['speed'])
        self.pitch.setValue(config['pitch'])
        self.bitrate.setCurrentText(config['bitrate'])

    def resume_generation(self):
        if self.busy or self.closing:
            return
        if self.pages.currentIndex() == PAGE_SRT:
            self.start_srt_generation()
            return
        if self.pages.currentIndex() == PAGE_QUEUE and any(i['state'] not in ('done', 'skipped') for i in self.txt_queue['items']):
            self.queue_start()
            return
        self.refresh_recovery()
        if not self.pending_job:
            self.start_generation()
            return
        job = self.pending_job
        try:
            manifest = read_json(job / 'manifest.json')
            config, text = manifest['config'], manifest['text']
            current = self.snapshot() if self.voice.currentData() else None
            if text != self.editor.toPlainText().strip() or current != config:
                if not self.confirm('Phục hồi tác vụ', 'Nạp lại nội dung và cấu hình của tác vụ dở? Nội dung đang soạn sẽ được thay thế.'):
                    return
                self.restore_config(config)
                self.editor.setPlainText(text)
            output = writable_directory(Path(self.output_dir.text()))
            name = audio_filename(self.filename.text()) if self.filename.text().strip() else Path(manifest['output_file']).name
            provider, _ = split_voice_id(config['speaker_id'])
            engine = self.new_engine(provider)
            self.navigate(0)
            self.run_job(engine, text, output / name, config, job)
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.error(str(error) if isinstance(error, ValueError) else 'Không phục hồi được tác vụ. Dữ liệu cũ được giữ lại.', 'resume')

    def open_voice_picker(self):
        saydi = [dict(voice, gender='Nữ', style='Thuyết minh')
                 for voice in STUDIO_VOICES if voice['engine'] == 'saydi']
        picker = VoicePicker(self, saydi + EVERAI_VOICES, self.voice.currentData(),
                             self.favorite_voices, self.toggle_favorite_by_id)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        provider, _ = split_voice_id(picker.selected_id)
        self.provider.setCurrentIndex(self.provider.findData(provider))
        self.search.clear()
        self.language.setCurrentIndex(0)
        self.region.setCurrentIndex(0)
        self.voice.setCurrentIndex(self.voice.findData(picker.selected_id))

    def preview(self):
        if self.busy:
            return
        try:
            config = self.snapshot()
            if self.provider_id() == 'everai':
                sample = voice_demo_path(config['speaker_id'])
                if not sample:
                    raise ValueError('Mẫu giọng chưa có hoặc bị hỏng. Hãy dùng bản đầy đủ bộ mẫu.')
                self.load_playback(sample, 'Mẫu gốc · ' + self.voice.currentText())
                self.play_pause()
                return
            digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode() + SAMPLE_TEXT.encode()).hexdigest()
            cache = writable_directory(self.store.root / 'samples')
            target = cache / (digest + '.mp3')
            if target.is_file() and target.stat().st_size:
                self.load_playback(target, 'Mẫu nghe thử')
                self.play_pause()
                return
            for old in sorted(cache.glob('*.mp3'), key=lambda p: p.stat().st_mtime)[:-19]:
                if old != self.playback_file:
                    old.unlink(missing_ok=True)
            engine = self.new_engine()
            root = cache / 'jobs' / digest
            pending = [p for p in root.glob('*/manifest.json') if read_json(p, {}).get('status') != 'completed']
            job = max(pending, key=lambda p: p.stat().st_mtime).parent if pending else root / uuid.uuid4().hex
            self.run_job(engine, SAMPLE_TEXT, target, config, job, preview=True)
        except (ValueError, OSError) as error:
            self.error(str(error) if isinstance(error, ValueError) else 'Không lưu được mẫu nghe thử.', 'preview')

    def import_text(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Nạp nội dung', '', 'Nội dung (*.txt *.srt);;Mọi file (*)')
        if path:
            self.load_text_file(path)

    def load_text_file(self, path, append=None):
        if self.busy:
            return
        if Path(path).suffix.lower() == '.srt':
            try:
                if self.srt_panel.project and not self.confirm('Nhập SRT', 'Tạo project mới từ SRT? Project hiện tại đã được lưu.'):
                    return
                project = SrtProject.create(self.store.root / 'srt-projects', path)
                self.srt_panel.set_project(project)
                self.source_name = Path(path).stem
                self.navigate(PAGE_SRT)
                self.status.setText('Đã nhập SRT theo timeline. Kiểm tra cue và bấm Tạo / xuất SRT.')
                self.save_state()
            except (OSError, ValueError) as error:
                self.error(str(error) if isinstance(error, ValueError) else 'Không lưu được project SRT.')
            return
        try:
            path = Path(path)
            is_srt = path.suffix.lower() == '.srt'
            timed = is_srt and self.provider_id() != 'everai'
            text = path.read_text('utf-8-sig') if not is_srt or timed else '\n'.join(i['text'] for i in parse_srt_file(path))
            if not text.strip():
                raise ValueError('File không có nội dung.')
            if self.editor.toPlainText().strip() and append is None:
                question = QMessageBox(self)
                question.setWindowTitle('Nạp nội dung')
                question.setText('Bạn muốn xử lý nội dung đang soạn như thế nào?')
                replace = question.addButton('Thay thế', QMessageBox.ButtonRole.AcceptRole)
                add = question.addButton('Nối thêm', QMessageBox.ButtonRole.ActionRole)
                question.addButton('Hủy', QMessageBox.ButtonRole.RejectRole)
                question.exec()
                if question.clickedButton() not in (replace, add):
                    return
                append = question.clickedButton() is add
            cursor = self.editor.textCursor()
            cursor.beginEditBlock()
            if not append:
                cursor.select(cursor.SelectionType.Document)
                cursor.removeSelectedText()
            else:
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText('\n')
            cursor.insertText(text)
            cursor.endEditBlock()
            self.source_name = path.stem
            self.navigate(0)
            self.status.setText('Đã nạp SRT theo timeline.' if timed else 'Đã nạp nội dung.')
        except (OSError, UnicodeError, ValueError):
            self.error('Không đọc được nội dung. Chọn TXT UTF-8 hoặc SRT hợp lệ.')

    def input_mode_changed(self):
        if hasattr(self, 'pages'):
            self.navigate(PAGE_SRT if self.input_mode.currentData() == 'srt' else PAGE_STUDIO)

    def srt_changed(self):
        if hasattr(self, 'generate_button'):
            self.update_stats()
            self.refresh_recovery()
            self.schedule_save()

    def srt_project_loaded(self):
        config = self.srt_panel.project.data.get('config') if self.srt_panel.project else None
        if config:
            try:
                self.restore_config(config)
            except ValueError as error:
                self.error(str(error), 'srt')

    def restore_srt_draft(self):
        draft = self.store.load('srt-draft.json', {})
        try:
            if not isinstance(draft, dict):
                raise ValueError('Bản nháp SRT không hợp lệ.')
            if draft.get('project'):
                self.srt_panel.set_project(SrtProject(draft['project']))
                if self.srt_panel.project.data.get('config'):
                    self.restore_config(self.srt_panel.project.data['config'])
            if draft.get('policy') in ('bounded', 'force'):
                self.srt_panel.policy.setCurrentIndex(self.srt_panel.policy.findData(draft['policy']))
            if isinstance(draft.get('max_tempo'), (float, int)):
                self.srt_panel.tempo.setValue(draft['max_tempo'])
            if draft.get('format') in ('wav', 'mp3'):
                self.srt_panel.output_format.setCurrentIndex(self.srt_panel.output_format.findData(draft['format']))
            self.srt_panel.restore_drafts(draft.get('cue_drafts', {}))
            if draft.get('mode') == 'srt':
                self.navigate(PAGE_SRT)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error('Không mở được project SRT đã lưu. Dữ liệu được giữ lại; dùng Mở project để chọn lại.')

    def preview_srt_cue(self, path):
        self.load_playback(Path(path), 'Cue SRT · ' + Path(path).name)
        self.play_pause()

    def srt_to_text(self, text):
        if self.editor.toPlainText().strip() and not self.confirm('Lấy lời thoại', 'Thay nội dung văn bản đang soạn bằng lời thoại SRT?'):
            return
        self.editor.setPlainText(text)
        self.navigate(PAGE_STUDIO)

    def regenerate_srt_cue(self, cue_id):
        if self.busy or not self.srt_panel.project:
            return
        cue = self.srt_panel.project.cue(cue_id)
        count = sum(map(len, srt_chunks_for(cue['text'], self.provider_id())))
        if not self.confirm('Tạo lại cue', f'Tạo giọng mới cho cue {cue["number"]}: {count:,} ký tự tính phí mới. Tiếp tục?'):
            return
        try:
            self.srt_panel.project.regenerate(cue_id)
            self.start_srt_generation(render_only=cue_id, confirmed=True)
        except (OSError, ValueError) as error:
            self.error(str(error), 'srt')

    def start_srt_generation(self, render_only=None, confirmed=False):
        if self.busy or self.closing or self.network_worker:
            return
        project = self.srt_panel.project
        try:
            if project is None:
                raise ValueError('Nhập file SRT hoặc mở project trước khi tạo.')
            if not self.srt_panel.apply_drafts():
                raise ValueError(self.srt_panel.message.text())
            if not ensure_ffmpeg():
                raise ValueError('Thiếu FFmpeg để xử lý SRT.')
            config = self.snapshot()
            provider, _ = split_voice_id(config['speaker_id'])
            # Capture all Qt values now: factory runs only on background threads.
            common = dict(proxy_catalog=self.proxy_catalog if provider == 'everai' else None,
                session_id=self.session_id.text().strip() or None, license_manager=self.license_manager,
                randomize_device=self.random_device.isChecked(),
                download_via_direct_ip=self.direct_download.isChecked(),
                temp_dir=self.store.root / 'samples/chunks', log_callback=self.log)
            saydi_token = self.saydi_token.text().strip()
            def factory(audio_config, concurrency):
                if provider == 'everai':
                    return EverAITTSEngine(**common, concurrency=concurrency)
                return MultiProviderTTSEngine(**common, concurrency=concurrency, input_mode='text',
                                              saydi_token=saydi_token)
            runner = SrtRunner(project, factory, self.license_manager, self.srt_workers.value(),
                               lambda kind, details: self.ui_queue.put((kind, details)))
            output_format = self.srt_panel.output_format.currentData()
            output = writable_directory(Path(self.output_dir.text()))
            title = self.filename.text() or project.data['name']
            if Path(title).suffix.lower() in ('.wav', '.mp3'):
                title = title[:-4]
            destination = output / Path(audio_filename(title)).with_suffix('.' + output_format)
            if not self.save_state():
                return
            options = dict(policy=self.srt_panel.policy.currentData(), max_tempo=self.srt_panel.tempo.value(),
                           output_format=output_format, render_only=render_only)
            self.engine = runner
            self.preview_running = False
            self.proxy_catalog.suspended = True
            self.set_busy(True)
            self.progress.setValue(0)
            self.status.setText('Đang chuẩn bị project SRT…')
            self._srt_render_only = render_only
            def prepare():
                with srt_project_lock(project.root):
                    project.reload()
                    estimate = runner.estimate(config, cue_id=render_only)
                    if estimate['needs_render']:
                        if self.license_manager is None:
                            raise LicenseError('Cần license để tạo giọng cho cue SRT.')
                        self.license_manager.ensure_active()
                        if provider != 'everai':
                            TTSService().preflight(provider)
                    return estimate
            worker = TaskThread(prepare, self)
            self.worker = worker
            worker.finished.connect(lambda: self.finish_srt_preflight(runner, destination, config, options, confirmed))
            worker.start()
        except (ValueError, OSError) as error:
            self.error(str(error) if isinstance(error, ValueError) else 'Không lưu được project/audio SRT.', 'srt')

    def finish_srt_preflight(self, runner, destination, config, options, confirmed):
        worker = self.worker
        if worker is None:
            return
        if worker.isRunning():
            QTimer.singleShot(20, lambda: self.finish_srt_preflight(runner, destination, config, options, confirmed))
            return
        if worker.error or runner.cancel_event.is_set() or self.closing:
            if not worker.error:
                worker.error = EverAICancelled('Đã dừng kiểm tra project SRT.')
            self.finish_srt_job()
            return
        estimate = worker.result
        allowed = True
        if estimate['remaining'] and not confirmed:
            allowed = self.confirm('Tạo giọng SRT',
                f'{estimate["cues"]:,} cue · {estimate["chunks"]:,} phân đoạn.\n'
                f'Còn {estimate["remaining"]:,} ký tự cần tạo/tính phí. Audio gốc có sẵn được dùng lại. Tiếp tục?')
        if allowed and options['policy'] == 'force':
            allowed = self.confirm('Nén mạnh SRT', 'Cho phép tăng tốc tới 4×? Câu vẫn dài sẽ đẩy phần sau và audio vẫn được xuất đủ lời.')
        if not allowed:
            worker.error = EverAICancelled('Đã hủy trước khi tạo giọng SRT.')
            self.finish_srt_job()
            return
        worker.deleteLater()
        worker = TaskThread(lambda: runner.process(destination, config, **options), self)
        self.worker = worker
        worker.finished.connect(self.finish_srt_job)
        worker.start()

    def finish_srt_job(self):
        worker = self.worker
        if worker is None:
            return
        if worker.isRunning():
            QTimer.singleShot(20, self.finish_srt_job)
            return
        result, error = worker.result, worker.error
        runner = self.engine
        self.worker = self.engine = None
        worker.deleteLater()
        self.proxy_catalog.suspended = False
        self.set_busy(False)
        for entry in runner.get_proxy_traffic_stats():
            try:
                self.store.record_proxy_traffic(entry.get('proxy_id'), entry.get('name'), entry.get('address'),
                    dict(proxy_tx_bytes=entry.get('tx_bytes', 0), proxy_rx_bytes=entry.get('rx_bytes', 0),
                         proxy_request_count=entry.get('request_count', 0), measurement=entry.get('measurement', 'unknown')))
            except (OSError, ValueError):
                self.log('Không lưu được lưu lượng tác vụ SRT.', 'WARNING')
        self.srt_panel.refresh()
        if error:
            if isinstance(error, EverAICancelled):
                message = 'Đã dừng SRT. Audio/chunk đã tạo được giữ lại; bấm Tiếp tục để phục hồi.'
            elif isinstance(error, ValueError):
                message = str(error)
            elif isinstance(error, OSError):
                message = 'Không ghi được audio/project SRT. Kiểm tra dung lượng và quyền ghi rồi tiếp tục.'
            elif isinstance(error, AudioAssemblyError):
                message = 'Không ghép được âm thanh. Chọn nơi lưu khác hoặc dùng bản ứng dụng có FFmpeg đầy đủ.'
            elif isinstance(error, EverAIQuotaError):
                message = 'Dịch vụ đang giới hạn chuyển đổi. Kiểm tra tài khoản hoặc thử lại sau.'
            else:
                message = 'Chưa tạo xong SRT. Audio/chunk đã tạo được giữ lại; kiểm tra kết nối rồi tiếp tục.'
            self.error(message, 'srt')
            self.srt_panel.message.setText(message)
        elif self._srt_render_only:
            self.preview_srt_cue(str(result))
            self.status.setText('Cue đã sẵn sàng. Bấm Tạo / xuất SRT để ghép toàn timeline.')
        else:
            self.last_output_file = Path(result)
            try:
                self.store.add_result(result, project_id=self.srt_panel.project.data['id'],
                    revision=self.srt_panel.project.data['revision'], format=Path(result).suffix[1:])
            except OSError:
                self.log('Không lưu được lịch sử SRT.', 'WARNING')
            self.load_playback(result)
            self.progress.setValue(1000)
            self.status.setText('Đã lưu timeline SRT · ' + Path(result).name)
            self.refresh_history()
        self.refresh_recovery()
        self.refresh_balance()
        if self.closing:
            self.close()

    def browse_output(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, 'Chọn nơi lưu âm thanh', self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def queue_import(self):
        if self.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, 'Thêm TXT vào hàng đợi', '', 'Văn bản UTF-8 (*.txt)')
        if files:
            self.add_queue_files(files)

    def add_queue_files(self, files):
        if self.busy:
            return False
        candidates = []
        try:
            for name in sorted(files, key=lambda value: [int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)', Path(value).name)]):
                text = Path(name).read_text('utf-8-sig').strip()
                if not text:
                    raise ValueError('File trống: ' + Path(name).name)
                candidates.append(dict(id=uuid.uuid4().hex, name=Path(name).name, text=text, state='wait'))
        except (OSError, UnicodeError, ValueError) as error:
            self.error(str(error) if isinstance(error, ValueError) else 'Không đọc được TXT UTF-8. Danh sách chưa thay đổi.')
            return False
        self.txt_queue['items'].extend(candidates)
        self.refresh_queue()
        self.navigate(PAGE_QUEUE)
        return True

    def refresh_queue(self):
        names = dict(wait='Chờ', running='Đang chạy', error='Đã dừng / lỗi', done='Hoàn tất', skipped='Bỏ qua')
        self.queue_table.setRowCount(len(self.txt_queue['items']))
        for row, item in enumerate(self.txt_queue['items']):
            for col, text in enumerate((row+1, item['name'], len(item['text']), names.get(item['state'], 'Chờ'))):
                self.queue_table.setItem(row, col, QTableWidgetItem(str(text)))
        config = self.txt_queue.get('config')
        self.queue_summary.setText('Cấu hình đã chốt: ' + config['speaker_id'] + ' · ' + config['model_id']
                                   if config else 'Chọn giọng và thông số ở Studio, rồi bắt đầu hàng đợi.')
        try:
            atomic_json(self.store.root / 'txt_queue.json', self.txt_queue)
        except OSError:
            self.queue_running = False
            self.error('Không lưu được hàng đợi. Chưa bắt đầu file tiếp theo.')
            return False
        self.refresh_recovery()
        return True

    def queue_move(self, offset):
        if self.busy:
            return
        row = self.queue_table.currentRow()
        target = row + offset
        items = self.txt_queue['items']
        if 0 <= row < len(items) and 0 <= target < len(items):
            items[row], items[target] = items[target], items[row]
            self.refresh_queue()
            self.queue_table.selectRow(target)

    def queue_sort(self):
        if not self.busy:
            self.txt_queue['items'].sort(key=lambda i: [int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)', i['name'])])
            self.refresh_queue()

    def queue_remove(self):
        if not self.busy and self.queue_table.currentRow() >= 0:
            del self.txt_queue['items'][self.queue_table.currentRow()]
            self.refresh_queue()

    def queue_skip(self):
        if self.busy:
            return
        row = self.queue_table.currentRow()
        if row >= 0:
            self.txt_queue['items'][row]['state'] = 'skipped'
            self.refresh_queue()

    def queue_start(self):
        if self.busy or self.closing:
            return
        remaining = [i for i in self.txt_queue['items'] if i['state'] not in ('done', 'skipped')]
        if not remaining:
            self.status.setText('Hàng đợi đã hoàn tất. Thêm TXT để bắt đầu lượt mới.')
            return
        try:
            if not any(i.get('job') for i in remaining):
                self.txt_queue['config'] = self.snapshot()
            self.queue_running = True
            self.queue_next()
        except (ValueError, OSError) as error:
            self.queue_running = False
            self.error(str(error) if isinstance(error, ValueError) else 'Không lưu được hàng đợi.')

    def queue_next(self):
        if self.busy or not self.queue_running or self.closing:
            return
        item = next((i for i in self.txt_queue['items'] if i['state'] not in ('done', 'skipped')), None)
        if item is None:
            self.queue_running = False
            self.status.setText('Đã hoàn tất hàng đợi TXT.')
            self.refresh_recovery()
            return
        try:
            config = self.txt_queue['config']
            provider, _ = split_voice_id(config['speaker_id'])
            engine = self.new_engine(provider)
            output = writable_directory(Path(self.output_dir.text()))
            item.setdefault('job', uuid.uuid4().hex)
            item['state'] = 'running'
            self.queue_current = item
            if not self.refresh_queue():
                raise OSError('Không lưu được hàng đợi.')
            self.run_job(engine, item['text'], output / audio_filename(Path(item['name']).stem), config,
                         self.store.root / 'jobs' / item['job'])
        except (ValueError, OSError, KeyError) as error:
            item['state'] = 'error'
            self.queue_current = None
            self.queue_running = False
            self.refresh_queue()
            self.error(str(error) if isinstance(error, ValueError) else 'Không bắt đầu được file. Dữ liệu hàng đợi được giữ lại.')

    def refresh_proxies(self):
        if not hasattr(self, 'proxy_table'):
            return
        items = self.proxy_catalog.get_all()
        self.proxy_table.setRowCount(len(items))
        for row, item in enumerate(items):
            parsed = parse_proxy(item.address)
            address = f'{parsed.host}:{parsed.port}' if parsed else 'Chưa hợp lệ'
            for col, text in enumerate((item.name, address, 'Đang dùng' if item.is_active else '—',
                                       item.current_ip or '—', item.status, f'{item.latency} ms' if item.latency is not None else '—')):
                cell = QTableWidgetItem(str(text))
                cell.setData(Qt.ItemDataRole.UserRole, item.id)
                self.proxy_table.setItem(row, col, cell)
        entries = [item for item in self.store.proxy_traffic_entries()
                   if item.get('proxy_id') != 'builtin-default']
        self.traffic_table.setRowCount(len(entries))
        for row, item in enumerate(entries):
            for col, text in enumerate((item['name'], f'{item["tx_bytes"]:,} B', f'{item["rx_bytes"]:,} B',
                                       item['request_count'], item['measurement'])):
                self.traffic_table.setItem(row, col, QTableWidgetItem(str(text)))

    def selected_proxy(self):
        row = self.proxy_table.currentRow()
        cell = self.proxy_table.item(row, 0) if row >= 0 else None
        return self.proxy_catalog.get_by_id(cell.data(Qt.ItemDataRole.UserRole)) if cell else None

    def edit_proxy(self, edit=False):
        if self.busy or self.network_worker:
            return
        original = self.selected_proxy() if edit else None
        if edit and original is None:
            self.error('Chọn một proxy để chỉnh sửa.')
            return
        item = copy.deepcopy(original) if original else ProxyItem()
        dialog = QDialog(self)
        dialog.setWindowTitle('Chỉnh sửa proxy' if original else 'Thêm proxy')
        dialog.resize(500, 420)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name = QLineEdit(item.name)
        address = QLineEdit(item.address)
        address.setEchoMode(QLineEdit.EchoMode.Password)
        address.setPlaceholderText('IP:Port hoặc IP:Port:User:Pass')
        kind = QComboBox()
        kind.addItem('Proxy tĩnh', 'static')
        kind.addItem('Proxy xoay', 'rotating')
        kind.setCurrentIndex(1 if item.kind == 'rotating' else 0)
        rotate = QLineEdit(item.rotate_url or '')
        rotate.setEchoMode(QLineEdit.EchoMode.Password)
        minutes = QSpinBox()
        minutes.setRange(1, 1440)
        minutes.setValue(item.rotate_minutes)
        auto = QCheckBox('Tự động xoay theo chu kỳ')
        auto.setChecked(item.auto_rotate)
        on429 = QCheckBox('Xoay khi Voice Pro giới hạn quota')
        on429.setChecked(item.rotate_on_429)
        show = QCheckBox('Hiện địa chỉ / link đang nhập')
        show.toggled.connect(lambda enabled: [w.setEchoMode(QLineEdit.EchoMode.Normal if enabled else QLineEdit.EchoMode.Password) for w in (address, rotate)])
        for title, widget in [('Tên', name), ('Loại', kind), ('Địa chỉ', address), ('Link xoay IP', rotate), ('Chu kỳ (phút)', minutes)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        for widget in (auto, on429, show):
            layout.addWidget(widget)
        info = label('Proxy mới được lưu nhưng chưa tự kích hoạt.', 'muted')
        info.setWordWrap(True)
        layout.addWidget(info)
        errors = label('', 'muted')
        errors.setWordWrap(True)
        layout.addWidget(errors)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            parsed = parse_proxy(address.text().strip())
            if not parsed or not parsed.host or not 1 <= parsed.port <= 65535:
                errors.setText('Địa chỉ proxy chưa hợp lệ.')
                return
            if kind.currentData() == 'rotating' and not rotate.text().strip().startswith(('http://', 'https://')):
                errors.setText('Link xoay cần bắt đầu bằng http:// hoặc https://.')
                return
            old_schedule = (item.address, item.rotate_url, item.rotate_minutes, item.auto_rotate)
            item.name = name.text().strip() or 'Proxy'
            item.address = address.text().strip()
            item.kind = kind.currentData()
            item.rotate_url = rotate.text().strip() if item.kind == 'rotating' else ''
            item.rotate_minutes = minutes.value()
            item.auto_rotate = auto.isChecked() and item.kind == 'rotating'
            item.rotate_on_429 = on429.isChecked()
            # Re-read live state rather than overwrite a newer scheduler update.
            current = self.proxy_catalog.get_by_id(item.id)
            if current:
                item.is_active = current.is_active
                for field in ('status', 'current_ip', 'latency', 'last_rotate_at', 'next_rotate_at'):
                    setattr(item, field, getattr(current, field))
            if not item.auto_rotate:
                item.next_rotate_at = None
            elif old_schedule != (item.address, item.rotate_url, item.rotate_minutes, item.auto_rotate):
                item.next_rotate_at = time.time() + item.rotate_minutes * 60
            try:
                self.proxy_catalog.add_or_update(item)
                dialog.accept()
                self.refresh_proxies()
            except OSError:
                errors.setText('Không lưu được proxy. Kiểm tra thư mục dữ liệu.')
        buttons.accepted.connect(save)
        dialog.exec()

    def activate_proxy(self):
        if self.busy or self.network_worker:
            return
        item = self.selected_proxy()
        if item:
            self.proxy_catalog.set_active(item.id)
            self.refresh_proxies()
            self.status.setText('Đã kích hoạt proxy ' + item.name)

    def delete_proxy(self):
        if self.busy or self.network_worker:
            return
        item = self.selected_proxy()
        if item and self.confirm('Xóa proxy', 'Xóa cấu hình proxy đã chọn?'):
            self.proxy_catalog.delete(item.id)
            self.refresh_proxies()

    def import_proxies(self):
        if self.busy or self.network_worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Nhập danh sách proxy', '', 'Văn bản (*.txt)')
        if not path:
            return
        try:
            candidates = []
            for raw in Path(path).read_text('utf-8-sig').splitlines():
                raw = raw.strip()
                if not raw or raw.startswith('#'):
                    continue
                if not parse_proxy(raw):
                    raise ValueError('Có dòng proxy không hợp lệ. Danh sách chưa thay đổi.')
                candidates.append(ProxyItem(name=f'Proxy {len(candidates)+1:02}', address=raw, is_active=False))
            with self.proxy_catalog._lock:
                self.proxy_catalog.items.extend(candidates)
            self.proxy_catalog.save()
            self.refresh_proxies()
        except (OSError, UnicodeError, ValueError) as error:
            self.error(str(error) if isinstance(error, ValueError) else 'Không đọc được file proxy.')

    def proxy_action(self, rotate):
        if self.busy or self.network_worker:
            return
        selected = self.selected_proxy() or self.proxy_catalog.get_active_proxy()
        if not selected:
            return
        item = copy.deepcopy(selected)
        self.network_cancel.clear()
        self.proxy_catalog.suspended = True
        self.status.setText('Đang đổi IP…' if rotate else 'Đang kiểm tra kết nối…')
        fn = (lambda: self.proxy_catalog.rotate_proxy(item, cancel_event=self.network_cancel)) if rotate else (
            lambda: self.proxy_catalog.check_proxy(item, cancel_event=self.network_cancel))
        worker = TaskThread(fn, self)
        self.network_worker = worker
        self.set_busy(False)
        def finish():
            if worker.isRunning():
                QTimer.singleShot(20, finish)
                return
            current = self.proxy_catalog.get_by_id(item.id)
            if current:
                for field in ('current_ip', 'latency', 'status', 'last_rotate_at', 'next_rotate_at', 'error_message'):
                    setattr(current, field, getattr(item, field))
                self.proxy_catalog.save()
            success = not worker.error and isinstance(worker.result, dict) and worker.result.get('ok')
            self.network_worker = None
            worker.deleteLater()
            self.proxy_catalog.suspended = False
            self.set_busy(False)
            self.status.setText('Kết nối proxy tốt.' if success else 'Không kết nối được proxy. Kiểm tra cấu hình / nhà cung cấp.')
            self.refresh_proxies()
            if self.closing:
                self.close()
        worker.finished.connect(finish)
        worker.start()

    def export_traffic(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Xuất lưu lượng', 'proxy-traffic.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['Proxy', 'TX bytes', 'RX bytes', 'Requests', 'Measurement'])
                for item in self.store.proxy_traffic_entries():
                    if item.get('proxy_id') == 'builtin-default':
                        continue
                    writer.writerow([item['name'], item['tx_bytes'], item['rx_bytes'], item['request_count'], item['measurement']])
        except OSError:
            self.error('Không xuất được CSV. Chọn nơi lưu khác.')

    def reset_traffic(self):
        if not self.busy and self.confirm('Đặt lại lưu lượng', 'Đặt lại toàn bộ bộ đếm lưu lượng?'):
            try:
                self.store.reset_proxy_traffic()
                self.refresh_proxies()
            except OSError:
                self.error('Không lưu được bộ đếm.')

    def refresh_history(self):
        self.store.recent = self.store._load_recent()
        entries = self.store.recent
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for col, text in enumerate((Path(entry['path']).name, entry.get('time', ''), entry['path'])):
                self.history_table.setItem(row, col, QTableWidgetItem(str(text)))

    def history_path(self):
        row = self.history_table.currentRow()
        return Path(self.store.recent[row]['path']) if 0 <= row < len(self.store.recent) else None

    def play_history(self):
        path = self.history_path()
        if path:
            self.last_output_file = path
            self.load_playback(path)
            self.play_pause()

    def open_history(self):
        path = self.history_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def remove_history(self):
        path = self.history_path()
        if path:
            try:
                self.store.remove_result(path)
                self.refresh_history()
            except OSError:
                self.error('Không cập nhật được lịch sử.')

    def load_playback(self, path, name=None):
        path = Path(path)
        if not path.is_file():
            self.error('Không còn file âm thanh ở vị trí cũ.')
            return
        self.playback_file = path
        self.playback_label.setText(name or path.name)
        self.playback_label.setToolTip(str(path))
        self.player.load(path)
        self.play_button.setEnabled(not bool(self.player.last_error))
        self.seek.setEnabled(not bool(self.player.last_error))
        self.open_button.setEnabled(bool(self.last_output_file))
        self.seek.setRange(0, max(1, self.player.get_duration_ms()))
        self.seek.setValue(0)
        self.return_result_button.setVisible(bool(self.last_output_file and path != self.last_output_file))
        self.external_button.setVisible(bool(self.player.last_error))

    def play_pause(self):
        if not self.playback_file:
            return
        if self.player.is_playing():
            self.player.pause()
        elif self.player._is_paused:
            self.player.resume()
        elif self.player.current_file == self.playback_file:
            self.player.start()
        else:
            self.player.play(self.playback_file)
        self.external_button.setVisible(bool(self.player.last_error))
        self.update_player()

    def update_player(self):
        if not self.playback_file:
            return
        duration = self.player.get_duration_ms()
        position = self.player.get_position_ms()
        self.seek.setMaximum(max(1, duration))
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.play_time.setText(self.clock(position) + ' / ' + self.clock(duration))
        playing = self.player.is_playing()
        self.play_button.setText('Tạm dừng' if playing else 'Tiếp tục' if self.player._is_paused else 'Nghe')
        self.play_button.setIcon(self.qt_icons['pause' if playing else 'play'])

    @staticmethod
    def clock(milliseconds):
        seconds = max(0, int(milliseconds)) // 1000
        return f'{seconds // 60:02}:{seconds % 60:02}'

    def return_to_result(self):
        if self.last_output_file:
            self.load_playback(self.last_output_file)
            self.play_pause()

    def open_output(self):
        if self.last_output_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output_file.parent)))

    def open_external(self):
        if self.playback_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.playback_file)))

    def refresh_balance(self):
        try:
            if self.license_manager is None:
                raise LicenseError(self.license_error)
            balance = self.license_manager.balance(include_expiry=True)
            self.balance_chip.setText('License hết hạn' if balance['expired'] else f'{balance["available"]:,} ký tự')
            self.balance_detail.setText(f'Tổng cấp: {balance["total"]:,} · Đã dùng: {balance["used"]:,} · '
                f'Đang giữ: {balance["held"]:,} · Khả dụng: {balance["available"]:,} ký tự\n'
                f'Hạn dùng: {expiry_label(balance["expires_at"])} (giờ Việt Nam)'
                + (f' · Số dư giữ lại: {balance["remaining"]:,}' if balance['expired'] else ''))
        except (ValueError, OSError) as error:
            self.balance_chip.setText('Kiểm tra bản quyền')
            self.balance_detail.setText(str(error))

    def apply_license(self, data):
        if self.busy or self.license_manager is None:
            return False
        try:
            added = self.license_manager.import_license(data)
            self.status.setText('Đã kích hoạt gói ký tự.' if added else 'License đã nhập trước đó; số dư giữ nguyên.')
            self.refresh_balance()
            return True
        except (ValueError, OSError) as error:
            self.error(str(error), 'license')
            return False

    def activate_license(self):
        if self.apply_license(self.license_key.text()):
            self.license_key.clear()

    def import_license(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Nhập license', '', 'License (*.everai-license *.voicepro-license)')
        if path:
            try:
                with Path(path).open('rb') as stream:
                    self.apply_license(stream.read(MAX_LICENSE_BYTES+1))
            except OSError:
                self.error('Không đọc được file license.')

    def backup_wallet(self):
        if self.busy or self.license_manager is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Sao lưu ví', 'Studio-wallet-backup.sqlite3', 'SQLite (*.sqlite3)')
        if path:
            try:
                self.license_manager.backup_wallet(path)
                self.status.setText('Đã sao lưu ví.')
            except (OSError, ValueError) as error:
                self.error(str(error))

    def copy_support(self):
        import platform
        QApplication.clipboard().setText(f'{APP_TITLE} / PyQt6\nWindows: {platform.release()}\n'
                                        f'Mã lỗi: {self.last_error_code or "none"}\nTrạng thái: {"busy" if self.busy else "idle"}')
        self.status.setText('Đã sao chép thông tin hỗ trợ, không có token hoặc nội dung bài.')

    def set_admin_password(self):
        if self.busy:
            return
        existing = self.store.settings.get('admin_hash', '')
        if existing:
            old, ok = QInputDialog.getText(self, 'Mật khẩu quản trị', 'Mật khẩu hiện tại:', QLineEdit.EchoMode.Password)
            if not ok or not verify_password(old, existing):
                self.error('Mật khẩu không đúng.')
                return
        password, ok = QInputDialog.getText(self, 'Mật khẩu quản trị', 'Mật khẩu mới (ít nhất 8 ký tự):', QLineEdit.EchoMode.Password)
        if not ok:
            return
        if len(password) < 8:
            self.error('Mật khẩu cần ít nhất 8 ký tự.')
            return
        repeated, ok = QInputDialog.getText(self, 'Mật khẩu quản trị', 'Nhập lại mật khẩu mới:', QLineEdit.EchoMode.Password)
        if ok and password == repeated:
            self.store.settings['admin_hash'] = password_hash(password)
            self.save_state()
        else:
            self.error('Hai mật khẩu chưa khớp.')

    def open_admin(self):
        encoded = self.store.settings.get('admin_hash', '')
        if not encoded:
            self.error('Đặt mật khẩu quản trị trước khi mở nhật ký.')
            return
        password, ok = QInputDialog.getText(self, 'Nhật ký quản trị', 'Nhập mật khẩu:', QLineEdit.EchoMode.Password)
        if not ok or not verify_password(password, encoded):
            self.error('Mật khẩu không đúng.')
            return
        content = '\n'.join(f'{stamp} [{level}] {message}' for stamp, level, message in self.log_buffer)
        self.text_dialog('Nhật ký quản trị', content)

    def show_notices(self):
        resource = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
        candidates = [resource / 'THIRD_PARTY_NOTICES.txt', Path(sys.executable).parent / 'THIRD_PARTY_NOTICES.txt']
        path = next((p for p in candidates if p.is_file()), None)
        try:
            content = path.read_text('utf-8') if path else 'Thông báo giấy phép được kèm trong gói phát hành.'
        except OSError:
            content = 'Không đọc được thông báo. Xem THIRD_PARTY_NOTICES.txt trong gói.'
        self.text_dialog('Giấy phép thành phần', content)

    def text_dialog(self, title, content):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(content)
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def confirm(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if self.worker or self.network_worker:
            if not self.closing and not self.confirm('Đóng ứng dụng', 'Dừng tác vụ và thoát? Các đoạn đã tạo và bản nháp được giữ lại.'):
                event.ignore()
                return
            self.closing = True
            self.stop_generation()
            self.network_cancel.set()
            event.ignore()
            return
        if not self.save_state():
            self.closing = False
            event.ignore()
            return
        self.poll_timer.stop()
        self.balance_timer.stop()
        self.save_timer.stop()
        self.proxy_catalog.stop_scheduler()
        self.player.close()
        self.session_id.clear()
        self.saydi_token.clear()
        self.logger.removeHandler(self.log_handler)
        self.log_handler.close()
        event.accept()


def launch_gui():
    if '--everai-srt-smoke' in sys.argv:
        try:
            from .srt_studio.smoke import run_smoke
        except ImportError:
            from srt_studio.smoke import run_smoke
        run_smoke()
        return 0
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QIcon(str(BRAND_LOGO)))
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    window = StudioWindow()
    window.show()
    if '--everai-qt-smoke' in sys.argv:
        QTimer.singleShot(500, window.close)
    return app.exec()


if __name__ == '__main__':
    sys.exit(launch_gui())
