"""SRT cue editor; all widget access stays on the Qt main thread."""
from pathlib import Path

from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableView, QAbstractItemView, QHeaderView, QLineEdit, QPlainTextEdit, QComboBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QFileDialog, QMessageBox, QSplitter, QScrollArea)

from .model import format_ms, timestamp_ms
from .store import SrtProject

STATES = dict(pending='Chờ tạo', rendering='Đang tạo', raw_ready='Có audio gốc', fitting='Đang căn',
              ready='Sẵn sàng', error='Lỗi tạo giọng', cancelled='Đã dừng', needs_attention='Cần kiểm tra')


class CueModel(QAbstractTableModel):
    headers = ('Cue', 'Bắt đầu', 'Kết thúc', 'Nội dung', 'Audio gốc', 'Tăng tốc', 'Trạng thái')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cues = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cues)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.cues):
            return None
        cue = self.cues[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return cue.get('error') or cue['text']
        if role == Qt.ItemDataRole.DisplayRole:
            raw = (cue.get('generation') or {}).get('raw', {})
            fit = cue.get('fit') or {}
            return (cue['number'], format_ms(cue['start_ms']), format_ms(cue['end_ms']),
                cue['text'].replace('\n', ' '), f'{raw["frames"]/48000:.2f} s' if raw.get('frames') else '—',
                f'{fit["tempo"]:.2f}×' if fit else '—', STATES.get(cue['state'], cue['state']))[index.column()]

    def refresh(self, cues):
        self.beginResetModel()
        self.cues = sorted(cues, key=lambda c: (c['start_ms'], c['source_order']))
        self.rows_by_id = {cue['id']: i for i, cue in enumerate(self.cues)}
        self.endResetModel()


class SrtPanel(QWidget):
    changed = pyqtSignal()
    draft_changed = pyqtSignal()
    project_loaded = pyqtSignal()
    preview_requested = pyqtSignal(str)
    regenerate_requested = pyqtSignal(str)
    plain_text_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.busy = False
        self._refreshing = False
        self._filling = False
        self._detail_cue_id = None
        self.drafts = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.title = QLabel('Nhập file SRT để bắt đầu')
        self.title.setWordWrap(True)
        toolbar.addWidget(self.title, 1)
        self.open_button = QPushButton('Mở project')
        self.open_button.clicked.connect(self.open_project)
        toolbar.addWidget(self.open_button)
        layout.addLayout(toolbar)
        options = QFormLayout()
        options.setSpacing(8)
        self.policy = QComboBox()
        self.policy.addItem('Tự động · luôn xuất', 'bounded')
        self.policy.addItem('Nén mạnh · tối đa 4×', 'force')
        self.policy.setAccessibleName('Chính sách căn thời gian SRT')
        self.policy.setToolTip('Luôn giữ đủ lời và xuất audio. Nếu câu vẫn dài sau khi nén, các câu sau sẽ được đẩy tiếp nối.')
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(1, 4)
        self.tempo.setDecimals(2)
        self.tempo.setSingleStep(.05)
        self.tempo.setValue(1.35)
        self.tempo.setSuffix(' ×')
        self.tempo.setAccessibleName('Giới hạn tăng tốc cue')
        self.output_format = QComboBox()
        self.output_format.addItem('WAV · chuẩn timeline', 'wav')
        self.output_format.addItem('MP3 · chia sẻ', 'mp3')
        self.output_format.setAccessibleName('Định dạng xuất SRT')
        options.addRow('Căn thời gian', self.policy)
        options.addRow('Tăng tốc tối đa', self.tempo)
        options.addRow('Định dạng', self.output_format)
        layout.addLayout(options)
        self.policy.currentIndexChanged.connect(self.options_changed)
        self.tempo.valueChanged.connect(self.options_changed)
        self.output_format.currentIndexChanged.connect(self.options_changed)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.model = CueModel(self)
        self.table.setModel(self.model)
        self.table.setAccessibleName('Danh sách cue SRT')
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(150)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(45)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        splitter.addWidget(self.table)
        detail = QWidget()
        form = QVBoxLayout(detail)
        form.setContentsMargins(0, 0, 0, 0)
        times = QFormLayout()
        self.start = QLineEdit()
        self.end = QLineEdit()
        self.start.setAccessibleName('Bắt đầu cue HH:MM:SS,mmm')
        self.end.setAccessibleName('Kết thúc cue HH:MM:SS,mmm')
        times.addRow('Từ', self.start)
        times.addRow('Đến', self.end)
        form.addLayout(times)
        self.text = QPlainTextEdit()
        self.text.setAccessibleName('Lời thoại cue được chọn')
        self.text.setPlaceholderText('Chọn một cue để sửa lời thoại hoặc thời gian.')
        self.text.setMinimumHeight(55)
        self.text.textChanged.connect(self.record_draft)
        self.start.textChanged.connect(self.record_draft)
        self.end.textChanged.connect(self.record_draft)
        form.addWidget(self.text)
        actions = QGridLayout()
        self.apply_button = QPushButton('Lưu cue')
        self.apply_button.clicked.connect(self.apply_cue)
        self.remove_button = QPushButton('Bỏ cue')
        self.remove_button.clicked.connect(self.remove_cue)
        self.raw_button = QPushButton('Nghe gốc')
        self.raw_button.clicked.connect(lambda: self.preview(False))
        self.fit_button = QPushButton('Nghe đã căn')
        self.fit_button.clicked.connect(lambda: self.preview(True))
        self.regenerate_button = QPushButton('Tạo lại cue…')
        self.regenerate_button.clicked.connect(self.regenerate)
        for i, widget in enumerate((self.apply_button, self.remove_button, self.raw_button, self.fit_button, self.regenerate_button)):
            actions.addWidget(widget, i // 2, i % 2)
        form.addLayout(actions)
        plain = QPushButton('Lấy lời thoại thành văn bản…')
        plain.clicked.connect(self.extract_text)
        form.addWidget(plain)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(detail)
        area.setMinimumHeight(310)
        splitter.addWidget(area)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.message = QLabel('Tự động bỏ khoảng lặng thừa, mượn khe nghỉ và căn tốc độ. Câu còn dài sẽ đẩy phần sau; audio luôn được xuất đủ lời.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.edit_controls = [self.start, self.end, self.text, self.apply_button, self.remove_button,
                              self.raw_button, self.fit_button, self.regenerate_button, plain]
        self.set_busy(False)

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.model.cues[rows[0].row()] if rows and self.project else None

    def selection_changed(self, *args):
        if self._refreshing:
            return
        cue = self.selected()
        if cue:
            self._filling = True
            self._detail_cue_id = cue['id']
            draft = self.drafts.get(cue['id'], {})
            self.start.setText(draft.get('start', format_ms(cue['start_ms'])))
            self.end.setText(draft.get('end', format_ms(cue['end_ms'])))
            self.text.setPlainText(draft.get('text', cue['text']))
            self._filling = False
            self.message.setText(cue.get('error') or 'Lưu thay đổi trước khi tạo. Đổi thời gian chỉ căn lại audio; đổi lời thoại cần TTS mới.')
        for widget in self.edit_controls:
            widget.setEnabled(bool(cue) and not self.busy)

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.open_button, self.policy, self.tempo, self.output_format):
            widget.setEnabled(not busy)
        self.tempo.setEnabled(not busy and self.policy.currentData() == 'bounded')
        for widget in self.edit_controls:
            widget.setEnabled(bool(self.selected()) and not busy)

    def set_project(self, project):
        self.project = project
        self.drafts = {}
        self._detail_cue_id = None
        self._refreshing = True
        for widget in (self.policy, self.tempo, self.output_format):
            widget.blockSignals(True)
        self.policy.setCurrentIndex(max(0, self.policy.findData(project.data['policy'])))
        self.tempo.setValue(project.data['max_tempo'])
        self.output_format.setCurrentIndex(max(0, self.output_format.findData(project.data['format'])))
        for widget in (self.policy, self.tempo, self.output_format):
            widget.blockSignals(False)
        self._refreshing = False
        self.refresh()
        if project.data['warnings']:
            self.message.setText('\n'.join(project.data['warnings'][:4]))
        self.changed.emit()
        self.project_loaded.emit()

    def refresh(self):
        cue = self.selected()
        selected_id = cue['id'] if cue else None
        self._refreshing = True
        if self.project:
            self._cue_source = self.project.data['cues']
            self.model.refresh(self.project.data['cues'])
            self.title.setText(f'{self.project.data["name"]} · {len(self.project.data["cues"]):,} cue')
            row = next((i for i, c in enumerate(self.model.cues) if c['id'] == selected_id), 0)
            if self.model.cues:
                self.table.selectRow(row)
        self._refreshing = False
        self.set_busy(self.busy)
        self.selection_changed()

    def sync_event(self, cue_id):
        if not self.project:
            return
        if getattr(self, '_cue_source', None) is not self.project.data['cues']:
            self.refresh()
        elif cue_id in self.model.rows_by_id:
            row = self.model.rows_by_id[cue_id]
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, 6))
            selected = self.selected()
            if selected and selected['id'] == cue_id:
                self.selection_changed()

    def options_changed(self, *args):
        self.tempo.setEnabled(not self.busy and self.policy.currentData() == 'bounded')
        if not self._refreshing:
            self.changed.emit()

    def record_draft(self):
        if self._filling or self._refreshing or self.busy or not self._detail_cue_id:
            return
        self.drafts[self._detail_cue_id] = dict(start=self.start.text(), end=self.end.text(),
                                              text=self.text.toPlainText())
        self.draft_changed.emit()

    def restore_drafts(self, drafts):
        ids = {c['id'] for c in self.project.data['cues']} if self.project else set()
        if isinstance(drafts, dict):
            self.drafts = {key: value for key, value in drafts.items() if key in ids and isinstance(value, dict)
                           and all(isinstance(value.get(k), str) for k in ('start', 'end', 'text'))}
        self.selection_changed()

    def apply_drafts(self):
        if self.busy or not self.project:
            return False
        try:
            changes = {key: dict(text=draft['text'], start_ms=timestamp_ms(draft['start']),
                                end_ms=timestamp_ms(draft['end'])) for key, draft in self.drafts.items()}
            if changes:
                self.project.edit_cues(changes)
                self.drafts = {}
                self.refresh()
                self.changed.emit()
            return True
        except (ValueError, OSError) as error:
            self.project.reload()
            self.message.setText(str(error))
            return False

    def open_project(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Mở project SRT', '', 'Project SRT (project.json)')
        if path:
            try:
                project = SrtProject(Path(path).parent)
                if self.project and QMessageBox.question(self, 'Mở project', 'Chuyển sang project đã chọn? Project hiện tại đã được lưu.') != QMessageBox.StandardButton.Yes:
                    return
                self.set_project(project)
            except (ValueError, OSError) as error:
                self.message.setText(str(error))

    def apply_cue(self):
        cue = self.selected()
        if not cue or self.busy:
            return False
        try:
            self.project.edit_cue(cue['id'], self.text.toPlainText(), timestamp_ms(self.start.text()), timestamp_ms(self.end.text()))
            self.drafts.pop(cue['id'], None)
            self.refresh()
            self.changed.emit()
            return True
        except (ValueError, OSError) as error:
            self.project.reload()
            self.message.setText(str(error))
            return False

    def remove_cue(self):
        cue = self.selected()
        if not cue or self.busy:
            return
        if QMessageBox.question(self, 'Bỏ cue', f'Bỏ cue {cue["number"]} khỏi timeline? Audio cũ vẫn được giữ.') != QMessageBox.StandardButton.Yes:
            return
        try:
            self.project.remove_cue(cue['id'])
            self.drafts.pop(cue['id'], None)
            self.refresh()
            self.changed.emit()
        except (ValueError, OSError) as error:
            self.message.setText(str(error))

    def preview(self, fitted):
        cue = self.selected()
        if not cue or self.busy:
            return
        try:
            if cue['id'] in self.drafts:
                raise ValueError('Lưu cue trước khi nghe để tránh nhầm với audio của nội dung cũ.')
            if fitted:
                path = self.project.path((cue.get('fit') or {}).get('file', ''))
            else:
                generation = cue.get('generation')
                if not generation or not generation.get('raw'):
                    raise ValueError('Cue chưa có audio gốc.')
                path = self.project.path(generation['raw_file']) if generation.get('raw_file') else self.project.generation_root(cue) / 'raw.mp3'
            if not path.is_file():
                raise ValueError('Không còn audio của cue. Hãy tạo/căn cue trước.')
            self.preview_requested.emit(str(path))
        except (ValueError, OSError, KeyError) as error:
            self.message.setText(str(error))

    def regenerate(self):
        cue = self.selected()
        if cue and not self.busy and self.apply_cue():
            self.regenerate_requested.emit(cue['id'])

    def extract_text(self):
        if self.project and not self.busy:
            self.plain_text_requested.emit('\n'.join(c['text'] for c in self.model.cues))
