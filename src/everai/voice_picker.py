"""Voice Pro catalog picker with offline previews."""

import math
import uuid

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

if __package__:
    from .audio_player import MiniAudioPlayer
    from .utils import voice_demo_path
    from .qt_theme import make_icon
    from .tts.languages import LANGUAGES
else:
    from audio_player import MiniAudioPlayer
    from utils import voice_demo_path
    from qt_theme import make_icon
    from tts.languages import LANGUAGES


# Page one follows the supplied Studio screenshots. IDs remain those of the
# existing Studio entries, whose catalog names sometimes differ from the cards.
FEATURED = (
    ('voice-5ea04bb0-8170-4e82', 'Minh Trung'),
    ('voice-9a0cb158-05f5-43fe', 'Vũ Thu Huệ'),
    ('voice-739e9501-c06f-4cbf', 'Adam'),
    ('voice-97509013-51cd-4874', 'Nữ Hong K…'),
    ('voice-74bb4b22-98a7-41a4', 'Nam Hong …'),
    ('voice-22848dd8-d8b8-4342', 'Chú Sáu'),
    ('voice-a4cd333e-aa78-4b66', 'Tào Tháo 2'),
    ('voice-3e5fafad-595b-46a0', 'Bắc Ngạn K…'),
    ('voice-7fefdfa8-beb0-4614', 'Tào Tháo 1'),
    ('voice-4b950958-29a4-4ee5', 'Lão Bá Nghị'),
    ('voice-f11fb65a-2dc6-4112', 'Tiểu Nhị'),
    ('voice-ab888766-b59f-402b', 'Giảng Đạo …'),
    ('voice-085cc114-947b-4442', 'Đức Kiên N…'),
    ('voice-0e62b161-5866-4e85', 'Giảng Đạo'),
    ('voice-ae92cfd4-1531-4ace', 'Đức Kiên'),
    ('voice-efff9285-aea8-45ba', 'Nữ Podcast'),
    ('voice-f9ae90f1-12e4-470d', 'Nhân Vật N…'),
)
# Catalog `style` sometimes holds an accent or an English tone; accents live in
# `region`, so drop them here and fold near-duplicates into one purpose.
STYLE_ALIASES = {
    'Mỹ': '', 'Australian': '', 'British': '', 'Indian': '',
    'Warm, Engaging': 'Ấm áp', 'Soft, Natural': 'Nhẹ nhàng', 'Deep, Velvety': 'Trầm ấm',
    'Đọc tin tức': 'Tin tức', 'Giọng nhân vật': 'Nhân vật', 'Nói chuyện': 'Trò chuyện',
    'Vlog': 'Vlog & Nội dung cá nhân',
}


def normalize_style(style):
    style = (style or '').strip()
    return STYLE_ALIASES.get(style, style)


PALETTES = (
    ('#053d49', '#5a2637', '#8af6f2', '#f8a4be'),
    ('#19295a', '#7d3f32', '#83aaff', '#ffc0a0'),
    ('#073941', '#681849', '#4bd9f6', '#ff77b4'),
    ('#153b3b', '#86583c', '#a2ded2', '#ffd5a1'),
    ('#461725', '#292044', '#f4ab5e', '#dda4ff'),
    ('#403719', '#77612e', '#f6df88', '#fff1c4'),
)


def wave_art(index):
    width = height = 38
    left, right, cool, warm = PALETTES[index % len(PALETTES)]
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0, QColor(left))
    gradient.setColorAt(1, QColor(right))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    circle = QPainterPath()
    circle.addEllipse(0, 0, width, height)
    painter.setClipPath(circle)
    painter.fillRect(pixmap.rect(), gradient)
    for band, color in enumerate((cool, warm)):
        for strand in range(16):
            tint = QColor(color)
            tint.setAlpha(35 + strand * 3)
            painter.setPen(QPen(tint, 0.75))
            path = QPainterPath()
            for x in range(0, width + 1, 4):
                phase = x / width * math.tau
                y = (height * 0.50 + (strand - 7.5) * 1.8
                     + 18 * math.sin(phase + strand * .09 + band * 2 + index * .4))
                if x == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.drawPath(path)
    painter.end()
    return pixmap


class VoicePicker(QDialog):
    def __init__(self, parent, voices, selected_id, favorites, toggle_favorite):
        super().__init__(parent)
        self.setWindowTitle('Chọn giọng · Voice Pro')
        self.setObjectName('voicePicker')
        self.selected_id = selected_id
        self.favorites = set(favorites)
        self.toggle_favorite = toggle_favorite
        self.player = MiniAudioPlayer(alias='VoicePicker_' + uuid.uuid4().hex)
        self.finished.connect(lambda _: self.player.close())
        self.playing_button = None
        self.preview_timer = QTimer(self, interval=200)
        self.preview_timer.timeout.connect(self.update_preview)
        voices = [dict(voice, language=self.voice_language(voice),
                       style=normalize_style(voice.get('style')),
                       region=voice.get('region') or '')
                  for voice in voices]
        self.names = dict(FEATURED)
        self.voices = voices
        self.page = 0
        area = parent.screen().availableGeometry()
        self.resize(min(780, area.width() - 32), min(820, int(area.height() * .9)))
        self.setStyleSheet("""
            QDialog#voicePicker, QWidget#voiceList {background:white;}
            QDialog#voicePicker QLabel {color:#111;font-family:"Segoe UI";}
            QDialog#voicePicker QScrollArea {background:white;border:0;}
            QFrame#voiceTile {background:white;border:1px solid #f0eaf8;border-radius:14px;}
            QFrame#voiceTile:hover {border-color:#e7dbf7;}
            QFrame#voiceTile[selected="true"] {border-color:#fc3e57;background:#fffbfa;}
            QDialog#voicePicker QPushButton {background:transparent;color:#6b7280;
                border:0;border-radius:9px;padding:4px 8px;font-family:"Segoe UI";}
            QDialog#voicePicker QPushButton:hover {background:#f5f5f5;color:#111;}
            QDialog#voicePicker QPushButton:disabled {color:#a4a8b0;}
            QDialog#voicePicker QPushButton#pickerTab {border-radius:0;font-size:15px;
                font-weight:600;border-bottom:2px solid transparent;padding:0 0 9px;}
            QDialog#voicePicker QPushButton#pickerTab:checked {color:#111;border-bottom-color:#111;}
            QDialog#voicePicker QPushButton#useVoice {background:#fe655d;color:white;font-weight:700;}
            QDialog#voicePicker QPushButton#useVoice:hover {background:#ec5851;}
            QDialog#voicePicker QPushButton#chosenVoice {background:#fff1f2;color:#be123c;}
            QDialog#voicePicker QPushButton#favoriteVoice[starred="true"] {color:#b77900;}
            QDialog#voicePicker QPushButton#pickerPage {border:1px solid #e5e7eb;color:#111;}
            QDialog#voicePicker QPushButton#pickerPage:disabled {color:#a4a8b0;}
            QDialog#voicePicker QPushButton#pickerPage:checked {background:#111;color:white;}
            QDialog#voicePicker QComboBox, QDialog#voicePicker QLineEdit {
                background:white;color:#111;border:1px solid #e5e7eb;border-radius:10px;
                padding:0 12px;font-family:"Segoe UI";font-size:14px;}
            QDialog#voicePicker QComboBox::drop-down {border:0;}
            QDialog#voicePicker QComboBox QAbstractItemView {background:white;color:#111;
                selection-background-color:#fff1f2;selection-color:#111;}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel('Chọn giọng')
        title.setStyleSheet('font-size:19px;font-weight:700;')
        header.addWidget(title, 1)
        close = QPushButton('×')
        close.setAccessibleName('Đóng')
        close.setFixedSize(34, 34)
        close.setStyleSheet('background:#f5f5f5;border-radius:17px;font-size:20px;')
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)

        tabs = QHBoxLayout()
        tabs.setSpacing(22)
        self.tabs = []
        for mode in ('Khám phá', 'Đã chọn', 'Giọng của tôi', 'Yêu thích'):
            tab = QPushButton(mode)
            tab.setObjectName('pickerTab')
            tab.setCheckable(True)
            tab.setAutoExclusive(True)
            tab.clicked.connect(self.reset_page)
            tabs.addWidget(tab)
            self.tabs.append(tab)
        self.tabs[0].setChecked(True)
        tabs.addStretch()
        layout.addLayout(tabs)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet('background:#e5e7eb;')
        layout.addWidget(divider)
        self.search = QLineEdit()
        self.search.setFixedHeight(50)
        self.search.setPlaceholderText('Nhập để tìm kiếm…')
        self.search.addAction(make_icon('search', color='#6b7280'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.reset_page)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        self.filters = []
        for field, title in (('language', 'Tất cả ngôn ngữ'), ('gender', 'Tất cả giới tính'),
                             ('region', 'Tất cả vùng'), ('style', 'Tất cả mục đích')):
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(9)
            combo.setFixedHeight(40)
            combo.addItem(title, None)
            for value in sorted({voice[field] for voice in self.voices if voice[field]},
                                key=lambda value: (value != 'Tiếng Việt', value.casefold())):
                combo.addItem(value, value)
            indicator = QLabel('⌄', combo)
            indicator.setStyleSheet('color:#6b7280;font-size:16px;')
            indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            indicator_layout = QHBoxLayout(combo)
            indicator_layout.setContentsMargins(0, 0, 12, 0)
            indicator_layout.addStretch()
            indicator_layout.addWidget(indicator)
            combo.currentIndexChanged.connect(self.reset_page)
            filters.addWidget(combo, 1)
            self.filters.append((field, combo))
        layout.addLayout(filters)
        self.status = QLabel()
        self.status.setStyleSheet('color:#6b7280;font-size:13px;')
        layout.addWidget(self.status)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)
        self.pagination = QHBoxLayout()
        self.pagination.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.pagination.setSpacing(4)
        layout.addLayout(self.pagination)
        self.render()

    @staticmethod
    def voice_language(voice):
        key = voice['id']
        code = voice.get('lang') or (key.split('_', 1)[0] if '_' in key else 'vi')
        code = {'jp': 'ja', 'kr': 'ko', 'cn': 'zh'}.get(code, code)
        if code not in LANGUAGES:
            code = 'vi'
        return f'Tiếng {LANGUAGES[code]}'

    def sort_key(self, voice, featured_view):
        rank = list(self.names).index(voice['id']) if voice['id'] in self.names else len(self.names)
        return (rank if featured_view else 0,
                voice['id'] not in self.favorites,
                voice['language'] != 'Tiếng Việt', voice['language'],
                voice['gender'], voice['name'].casefold())

    def reset_page(self, *_):
        self.page = 0
        self.render()

    def change_page(self, offset):
        self.page += offset
        self.render()

    def render(self):
        self.stop_preview()
        query = self.search.text().strip().casefold()
        filters = {field: combo.currentData() for field, combo in self.filters}
        matches = []
        for voice in self.voices:
            key = voice['id']
            name = self.names.get(key, voice['name'])
            if self.tabs[1].isChecked() and key != self.selected_id:
                continue
            if self.tabs[2].isChecked():
                continue
            if self.tabs[3].isChecked() and key not in self.favorites:
                continue
            if query not in f"{name} {voice['name']} {key}".casefold():
                continue
            if any(value and voice[field] != value for field, value in filters.items()):
                continue
            matches.append(voice)
        featured_view = self.tabs[0].isChecked() and not (query or any(filters.values()))
        matches.sort(key=lambda voice: self.sort_key(voice, featured_view))
        page_size = len(FEATURED)
        pages = max(1, math.ceil(len(matches) / page_size))
        first = self.page * page_size
        self.status.setText(('Giọng hàng đầu · ' if featured_view and self.page == 0 else '')
                            + f'{len(matches)} giọng')
        self.render_pages(pages)
        container = QWidget()
        container.setObjectName('voiceList')
        rows = QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 5, 0)
        rows.setSpacing(8)
        for index, voice in enumerate(matches[first:first + page_size]):
            rows.addWidget(self.tile(voice, first + index))
        if not matches:
            message = 'Chưa có giọng của tôi.' if self.tabs[2].isChecked() else 'Không tìm thấy giọng phù hợp.'
            rows.addWidget(QLabel(message))
        rows.addStretch()
        self.scroll.setWidget(container)
        self.scroll.verticalScrollBar().setValue(0)

    def render_pages(self, pages):
        while self.pagination.count():
            self.pagination.takeAt(0).widget().deleteLater()
        for caption, target in [('‹', self.page - 1),
                                *((str(i + 1), i) for i in range(pages)),
                                ('›', self.page + 1)]:
            button = QPushButton(caption)
            button.setObjectName('pickerPage')
            button.setFixedSize(36, 36)
            button.setEnabled(0 <= target < pages)
            button.setCheckable(caption.isdecimal())
            button.setChecked(button.isCheckable() and self.page == target)
            button.clicked.connect(lambda _, target=target: self.change_page(target - self.page))
            self.pagination.addWidget(button)

    def tile(self, voice, index):
        key = voice['id']
        name = self.names.get(key, voice['name'])
        row = QFrame()
        row.setObjectName('voiceTile')
        row.setProperty('selected', key == self.selected_id)
        row.setToolTip(f"{voice['name']} · {key}")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        avatar = QLabel()
        avatar.setPixmap(wave_art(index))
        avatar.setFixedSize(38, 38)
        layout.addWidget(avatar)
        info = QVBoxLayout()
        info.setContentsMargins(7, 0, 4, 0)
        info.setSpacing(1)
        title = QLabel(name)
        title.setStyleSheet('font-size:14px;font-weight:500;')
        parts = []
        for field in ('gender', 'region', 'language', 'style'):
            if voice[field] and voice[field] not in parts:
                parts.append(voice[field])
        details = QLabel(' · '.join(parts))
        details.setStyleSheet('color:#6b7280;font-size:11px;')
        for label in (title, details):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            info.addWidget(label)
        layout.addLayout(info, 1)

        use = QPushButton('Đã chọn' if key == self.selected_id else 'Dùng')
        use.setObjectName('chosenVoice' if key == self.selected_id else 'useVoice')
        use.setFixedHeight(30)
        use.setAccessibleName(f'Dùng giọng {name}')
        use.clicked.connect(lambda: self.choose(key))
        play = QPushButton(make_icon('play', color='#6b7280'), '')
        play.setAccessibleName(f'Nghe thử {name}')
        if voice.get('engine') == 'saydi':
            play.setEnabled(False)
            play.setToolTip('Chọn Dùng, rồi bấm Nghe thử trong Studio để tạo mẫu Saydi.')
        play.clicked.connect(lambda: self.preview(key, play))
        copy = QPushButton(make_icon('copy', color='#6b7280'), '')
        copy.setToolTip('Sao chép ID giọng')
        copy.setAccessibleName(f'Sao chép ID giọng {name}')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(key))
        star = QPushButton('★' if key in self.favorites else '☆')
        star.setObjectName('favoriteVoice')
        star.setProperty('starred', key in self.favorites)
        star.setAccessibleName(f'Đổi yêu thích cho giọng {name}')
        star.clicked.connect(lambda: self.favorite(key, star))
        layout.addWidget(use)
        for button in (play, copy, star):
            button.setFixedSize(34, 34)
            button.setStyleSheet('font-size:17px;')
            layout.addWidget(button)
        return row

    def favorite(self, key, button):
        if not self.toggle_favorite(key):
            self.status.setText('Không lưu được giọng yêu thích.')
            return
        self.favorites ^= {key}
        if self.tabs[3].isChecked():
            self.reset_page()
        else:
            starred = key in self.favorites
            button.setText('★' if starred else '☆')
            button.setProperty('starred', starred)
            button.style().unpolish(button)
            button.style().polish(button)

    def choose(self, key):
        self.selected_id = key
        self.accept()

    def preview(self, key, button):
        was_playing = button is self.playing_button
        self.stop_preview()
        if was_playing:
            return
        sample = voice_demo_path(key)
        if not sample or not self.player.play(sample):
            self.status.setText('Không phát được mẫu giọng này.')
            return
        self.playing_button = button
        button.setIcon(make_icon('stop', color='#fc3e57'))
        self.preview_timer.start()

    def update_preview(self):
        if not self.player.is_playing():
            self.stop_preview()

    def stop_preview(self):
        self.preview_timer.stop()
        self.player.stop()
        if self.playing_button is not None:
            self.playing_button.setIcon(make_icon('play', color='#6b7280'))
            self.playing_button = None
