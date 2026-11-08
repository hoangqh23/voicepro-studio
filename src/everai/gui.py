"""EverAI's single-task desktop UI. Network and synthesis workers never call Tk."""
import os
import sys
import json
import time
import uuid
import queue
import logging
import platform
import threading
import subprocess
import hashlib
import copy
import csv
import re
from collections import deque
from pathlib import Path
from logging.handlers import RotatingFileHandler
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

try:
    from .voices import EVERAI_VOICES, DEFAULT_VOICE_ID
    from .engine import EverAITTSEngine
    from .client import EverAIError, EverAIConnectionError, EverAIQuotaError, EverAICancelled
    from .proxy_manager import ProxyCatalog
    from .audio_player import MiniAudioPlayer
    from .utils import (StudioStore, application_data_dir, writable_directory, atomic_json, read_json,
                        split_text_into_chunks, parse_srt_file, audio_filename, normalized_search,
                        ensure_ffmpeg, redact_log, AudioAssemblyError, parse_proxy, voice_demo_path, format_bytes)
    from .settings_dialog import SettingsDialog
    from .admin_log_dialog import AdminLogDialog
    from .license_manager import LicenseManager, LicenseError
    from .license_dialog import LicensePanel
    from .utils import normalize_tts_text
    from .ui_theme import StudioButton, Card, BACKGROUND, ACCENT, TEXT, MUTED, BORDER
    from .tts.service import studio_voices, split_voice_id, TTSService
    from .tts.engine import MultiProviderTTSEngine
    from .tts.srt import parse_srt as parse_tts_srt, split_srt_chunks
    from .tts.languages import language_label, language_code
except ImportError:
    from voices import EVERAI_VOICES, DEFAULT_VOICE_ID
    from engine import EverAITTSEngine
    from client import EverAIError, EverAIConnectionError, EverAIQuotaError, EverAICancelled
    from proxy_manager import ProxyCatalog
    from audio_player import MiniAudioPlayer
    from utils import (StudioStore, application_data_dir, writable_directory, atomic_json, read_json,
                       split_text_into_chunks, parse_srt_file, audio_filename, normalized_search,
                       ensure_ffmpeg, redact_log, AudioAssemblyError, parse_proxy, voice_demo_path, format_bytes)
    from settings_dialog import SettingsDialog
    from admin_log_dialog import AdminLogDialog
    from license_manager import LicenseManager, LicenseError
    from license_dialog import LicensePanel
    from utils import normalize_tts_text
    from ui_theme import StudioButton, Card, BACKGROUND, ACCENT, TEXT, MUTED, BORDER
    from tts.service import studio_voices, split_voice_id, TTSService
    from tts.engine import MultiProviderTTSEngine
    from tts.srt import parse_srt as parse_tts_srt, split_srt_chunks
    from tts.languages import language_label, language_code

SAMPLE_TEXT = "Xin chào, chúc bạn một ngày tốt lành. Đây là giọng đọc bạn đang lựa chọn."
PRIMARY_PROVIDER_LABEL = 'Voice Pro - chất lượng cao nhất'
PRIMARY_MODEL_PREFIX = 'everai-'
STUDIO_VOICES = [dict(v, engine='everai', lang='vi') for v in EVERAI_VOICES] + studio_voices()


def model_label(model):
    return PRIMARY_MODEL_PREFIX + model[len('everai-'):] if model.startswith('everai-') else model


def speech_chunks(text, provider):
    split = lambda value: split_text_into_chunks(normalize_tts_text(value), max_length=450)
    return split_srt_chunks(text, split)[0] if provider != 'everai' and '-->' in text else split(text)


class EverAIGuiApp:
    def __init__(self, root, data_dir=None):
        self.root = root
        root.title("Voice Pro - chất lượng cao nhất")
        root.geometry(f"{max(900, min(1180, root.winfo_screenwidth() - 80))}x{max(600, min(800, root.winfo_screenheight() - 100))}")
        root.minsize(900, 600)
        root.configure(bg=BACKGROUND)
        self.store = StudioStore(data_dir or application_data_dir())
        self.license_error = ""
        try:
            self.license_manager = LicenseManager(self.store.root)
        except (LicenseError, OSError) as error:
            self.license_manager = None
            self.license_error = str(error)
        self.ui_queue = queue.Queue()
        self.log_buffer = deque(maxlen=2000)
        self.thread = None
        self.outcome = None
        self.busy = None
        self.closing = False
        self.current_engine = None
        self.pending_job = None
        self.last_output_file = None
        self.playback_file = None
        self.source_name = ""
        self.session_id = None
        self.network_busy = False
        self.network_cancel = threading.Event()
        self.save_id = None
        self.restoring = True
        self.last_error_code = ""
        self.txt_queue = self.store.load('txt_queue.json', dict(items=[], config=None))
        if not isinstance(self.txt_queue, dict) or not isinstance(self.txt_queue.get('items'), list):
            self.txt_queue = dict(items=[], config=None)
        self.txt_queue['items'] = [i for i in self.txt_queue['items'] if isinstance(i, dict)
                                   and all(isinstance(i.get(k), str) for k in ('id', 'name', 'text', 'state'))]
        for item in self.txt_queue['items']:
            if item['state'] == 'running':
                item['state'] = 'error'
        self.queue_running = False
        self.queue_current = None
        self.player = MiniAudioPlayer(alias="EverAI_" + uuid.uuid4().hex)
        logs = self.store.root / "logs"
        logs.mkdir(exist_ok=True)
        self.logger = logging.getLogger("everai.studio." + uuid.uuid4().hex)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        handler = RotatingFileHandler(logs / "studio.log", maxBytes=1024 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        # StudioStore creates/migrates this file before the legacy catalog can seed demo data.
        self.proxy_catalog = ProxyCatalog(self.store.root / "proxies.json", self.log)
        self.store.warnings.extend(self.proxy_catalog.warnings)
        self.proxy_catalog.suspended = False
        self._build_ui()
        self._restore()
        self._queue_refresh()
        self.restoring = False
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-o>", lambda _: self._load_file())
        root.bind("<Control-Return>", lambda _: self._start_generation())
        self.poll_id = root.after(80, self._poll)
        self.txt_input.focus_set()

    def _build_ui(self):
        style = ttk.Style(self.root)
        ctk.set_appearance_mode("light")
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="white", foreground="#252a32")
        style.configure("TFrame", background="white")
        style.configure("TLabel", background="white", foreground=TEXT)
        style.configure("Shell.TFrame", background=BACKGROUND)
        style.configure("Shell.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure("Brand.TLabel", background=BACKGROUND, foreground=TEXT, font=("Segoe UI", 17, "bold"))
        style.configure("TButton", padding=(10, 6), background="#f8fafc", bordercolor="#dce2ec",
                        lightcolor="#f8fafc", darkcolor="#f8fafc", relief="flat")
        style.map("TButton", background=[("active", "#eaf1ff"), ("disabled", "#f1f4f8")],
                  foreground=[("disabled", "#9aa4b2")])
        style.configure("TEntry", fieldbackground="white", bordercolor=BORDER, padding=5)
        style.configure("TCombobox", fieldbackground="white", background="#f8fafc", bordercolor=BORDER, padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", "white")])
        style.configure("Horizontal.TScale", troughcolor="#e8edf5", background="#246bfd", borderwidth=0)
        style.configure("Horizontal.TProgressbar", troughcolor="#e8edf5", background="#246bfd", thickness=5, borderwidth=0)
        style.layout("Horizontal.TProgressbar", [("Horizontal.Progressbar.trough", {"sticky": "nswe", "children": [
            ("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Error.TLabel", foreground="#a12121")
        style.configure("Primary.TButton", foreground="white", background="#246bfd", padding=(20, 8))
        style.map("Primary.TButton", background=[("disabled", "#adb7c2"), ("active", "#1853cc")])
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=0,
                        bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND)
        style.layout("TNotebook.Tab", [])
        style.configure("TNotebook.Tab", padding=(16, 7), background="#e9edf4")
        style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", "#246bfd")])
        style.configure("TLabelframe", bordercolor=BORDER, background="white", relief="flat", borderwidth=0)
        style.configure("Treeview", rowheight=32, borderwidth=0, background="white", fieldbackground="white", foreground=TEXT)
        style.configure("Treeview.Heading", background="#f3f6fc", foreground=MUTED, relief="flat", padding=(10, 8))
        style.map("Treeview", background=[("selected", "#eaf0ff")], foreground=[("selected", ACCENT)])
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        self.root.option_add("*Text.Font", ("Segoe UI", 11))
        outer = ttk.Frame(self.root, padding=18, style="Shell.TFrame")
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer, style="Shell.TFrame")
        header.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(header, text="E", width=40, height=40, corner_radius=11, fg_color=ACCENT, bg_color=BACKGROUND,
                     text_color="white", font=("Segoe UI", 24, "bold")).pack(side="left", padx=(0, 12))
        branding = ttk.Frame(header, style="Shell.TFrame")
        branding.pack(side="left")
        ttk.Label(branding, text="Voice Pro - chất lượng cao nhất", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(branding, text="Không gian sáng tạo giọng nói", style="Shell.TLabel").pack(anchor="w")
        self.settings_button = StudioButton(header, text="⚙  Cài đặt", command=self.open_settings)
        self.settings_button.pack(side="right")
        self.balance_summary = tk.StringVar(value="Bản quyền")
        StudioButton(header, textvariable=self.balance_summary, style="Balance.TButton", width=165,
                     command=self.open_license).pack(side="right", padx=10)
        self._build_bottom(outer)
        navigation = ttk.Frame(outer, style="Shell.TFrame")
        navigation.pack(fill="x", pady=(0, 12))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.generation_tab, self.proxy_tab, self.history_tab = [ttk.Frame(self.tabs, style="Shell.TFrame") for _ in range(3)]
        self.nav_buttons = {}
        for tab, label in ((self.generation_tab, "Tạo giọng nói"), (self.proxy_tab, "Quản lý proxy"),
                           (self.history_tab, "Lịch sử")):
            self.tabs.add(tab, text=label)
            button = StudioButton(navigation, text=label, width=145, command=lambda tab=tab: self.tabs.select(tab))
            button.pack(side="left", padx=(0, 8))
            self.nav_buttons[str(tab)] = button
        self.tabs.bind("<<NotebookTabChanged>>", self._update_navigation)
        self._update_navigation()
        self.tabs.enable_traversal()
        main = self.generation_tab
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0, minsize=370)
        main.rowconfigure(0, weight=1)
        editor_card = Card(main, "Nội dung văn bản")
        editor_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left = editor_card.body
        left.rowconfigure(3, weight=1)
        left.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(left)
        toolbar.grid(row=0, column=0, sticky="ew")
        self.import_button = StudioButton(toolbar, text="Nạp file…", command=self._load_file)
        self.srt_button = StudioButton(toolbar, text="Nạp SRT", width=85, command=lambda: self._load_file("srt"))
        self.srt_button.pack(side="right", padx=4)
        self.txt_button = StudioButton(toolbar, text="Nạp TXT", width=85, command=lambda: self._load_file("txt"))
        self.txt_button.pack(side="right")
        self.clear_button = StudioButton(toolbar, text="Xóa", width=60, command=self._clear_text)
        self.clear_button.pack(side="right", padx=6)
        textframe = ttk.Frame(left)
        textframe.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.txt_input = tk.Text(textframe, undo=True, wrap="word", relief="flat", borderwidth=0,
                                 padx=2, pady=10, bg="white", fg=TEXT, insertbackground=ACCENT,
                                 selectbackground="#e1ebff", selectforeground=TEXT, spacing1=3, spacing3=6,
                                 highlightthickness=0, width=25, height=8)
        scroll = ctk.CTkScrollbar(textframe, command=self.txt_input.yview, width=8, fg_color="white",
                                  button_color="#dbe3f1", button_hover_color="#b9c8e6")
        scroll.pack(side="right", fill="y")
        self.txt_input.configure(yscrollcommand=scroll.set)
        self.txt_input.pack(fill="both", expand=True)
        self.stats = tk.StringVar(value="0 ký tự")
        ttk.Label(left, textvariable=self.stats, style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.txt_input.bind("<<Modified>>", self._modified)
        self.input_mode = tk.StringVar(value='queue' if self.txt_queue['items'] else 'text')
        modes = ttk.Frame(left)
        modes.grid(row=1, column=0, sticky='ew')
        ttk.Radiobutton(modes, text='Nhập văn bản', variable=self.input_mode, value='text', command=self._queue_refresh).pack(side='left')
        ttk.Radiobutton(modes, text='Hàng đợi TXT', variable=self.input_mode, value='queue', command=self._queue_refresh).pack(side='left')
        StudioButton(modes, text='Thêm TXT…', command=self._queue_import).pack(side='right')
        self.queue_controls = list(modes.winfo_children())
        self.editor_frame = textframe
        self.queue_panel = ttk.Frame(left)
        self.queue_panel.grid(row=2, column=0, sticky='ew')
        queue_table = ttk.Frame(self.queue_panel)
        queue_table.pack(fill='both', expand=True)
        self.queue_tree = ttk.Treeview(queue_table, columns=('order', 'name', 'chars', 'state'), show='headings', height=4)
        for key, label, width in [('order', 'STT', 40), ('name', 'File', 180), ('chars', 'Ký tự', 80), ('state', 'Trạng thái', 100)]:
            self.queue_tree.heading(key, text=label)
            self.queue_tree.column(key, width=width)
        queue_scroll = ttk.Scrollbar(queue_table, orient='vertical', command=self.queue_tree.yview)
        queue_scroll.pack(side='right', fill='y')
        self.queue_tree.pack(side='left', fill='both', expand=True)
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        actions = ttk.Frame(self.queue_panel)
        actions.pack(fill='x')
        for label, callback in [('↑', lambda: self._queue_move(-1)), ('↓', lambda: self._queue_move(1)), ('Bỏ file', self._queue_remove), ('Bỏ qua lỗi', self._queue_skip)]:
            button = StudioButton(actions, text=label, width=75, command=callback)
            button.pack(side='left', padx=2)
            self.queue_controls.append(button)
        self.queue_summary = tk.StringVar()
        ttk.Label(self.queue_panel, textvariable=self.queue_summary, wraplength=420, style='Muted.TLabel').pack(fill='x')
        rightframe = ttk.Frame(main, style="Shell.TFrame")
        rightframe.grid(row=0, column=1, sticky="nsew")
        self._scroll_areas = []
        self.workspace_canvas, self.workspace = self._scrollable(rightframe)
        voice_card = Card(self.workspace, "Chọn giọng đọc", tint="#def1e9", accent="#17654c")
        voice_card.pack(fill="x")
        right = voice_card.body
        self.controls = []
        self.tts_provider = tk.StringVar(value=PRIMARY_PROVIDER_LABEL)
        ttk.Label(right, text="Dịch vụ TTS", style="Muted.TLabel").pack(anchor="w")
        self.provider_combo = ttk.Combobox(right, textvariable=self.tts_provider, state="readonly",
                                          values=(PRIMARY_PROVIDER_LABEL, 'CapCut'))
        self.provider_combo.pack(fill="x", pady=(0, 5))
        self.controls.append(self.provider_combo)
        self.language = tk.StringVar(value='Tất cả')
        ttk.Label(right, text='Ngôn ngữ', style='Muted.TLabel').pack(anchor='w')
        self.language_combo = ttk.Combobox(right, textvariable=self.language, state='readonly',
                                           values=['Tất cả'], height=10)
        self.language_combo.pack(fill='x', pady=(0, 5))
        self.controls.append(self.language_combo)
        self.search = tk.StringVar()
        ttk.Label(right, text="Tìm giọng / vùng miền", style="Muted.TLabel").pack(anchor="w", pady=(0, 2))
        search_row = ttk.Frame(right)
        search_row.pack(fill="x", pady=(0, 6))
        search_entry = ctk.CTkEntry(search_row, textvariable=self.search, width=120, height=34,
                                    placeholder_text="Tìm tên giọng…", corner_radius=8, border_width=1,
                                    fg_color="#f8faff", border_color=BORDER, text_color=TEXT)
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.controls.append(search_entry)
        self.region = tk.StringVar(value="Tất cả")
        self.region_combo = ctk.CTkComboBox(search_row, variable=self.region, state="readonly", width=106, height=34,
                                         corner_radius=8, border_width=1, fg_color="white", border_color=BORDER,
                                         button_color="#eef3fc", button_hover_color="#e1eafd", text_color=TEXT,
                                         values=["Tất cả"] + sorted({v["region"] for v in EVERAI_VOICES}))
        self.region_combo.pack(side="right")
        self.voice = tk.StringVar()
        style.configure("Voice.TCombobox", padding=(8, 9), fieldbackground="#f0f8f4",
                        background="#deeee5", bordercolor="#a6cbb8")
        style.map("Voice.TCombobox", fieldbackground=[("readonly", "#f0f8f4")])
        self.cbo_voice = ttk.Combobox(right, textvariable=self.voice, state="readonly",
                                      height=8, style="Voice.TCombobox")
        self.cbo_voice.pack(fill="x")
        sample_actions = ttk.Frame(right)
        sample_actions.pack(fill="x", pady=(4, 2))
        self.preview_button = StudioButton(sample_actions, text="▶  Nghe mẫu", command=self._preview)
        self.preview_button.pack(side="left")
        self.preview_description = tk.StringVar(value="Nghe mẫu gốc miễn phí.")
        ttk.Label(right, textvariable=self.preview_description,
                  style="Muted.TLabel", wraplength=330).pack(anchor="w", pady=(0, 2))
        parameters_card = Card(self.workspace, "Thiết lập giọng nói", tint="#ebe4f9", accent="#65409b")
        parameters_card.pack(fill="x", pady=(12, 0))
        right = parameters_card.body
        self.advanced = ttk.Frame(right)
        self.advanced.pack(fill="x", pady=2)
        self.advanced.columnconfigure((0, 1), weight=1, uniform="parameters")
        self.bitrate, self.model = tk.StringVar(), tk.StringVar()
        ttk.Label(self.advanced, text="Mô hình").grid(row=0, column=0, sticky="w")
        ttk.Label(self.advanced, text="Chất lượng").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.model_combo = ctk.CTkComboBox(self.advanced, variable=self.model, state="readonly", width=140, height=34,
                                       corner_radius=8, border_width=1, fg_color="white", border_color=BORDER,
                                       button_color="#eef3fc", button_hover_color="#e1eafd", text_color=TEXT,
                                       values=(model_label("everai-v1.6"), model_label("everai-v1.5")))
        self.model_combo.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        self.bitrate_combo = ctk.CTkComboBox(self.advanced, variable=self.bitrate, state="readonly", height=34,
                                         corner_radius=8, border_width=1, fg_color="white", border_color=BORDER,
                                         button_color="#eef3fc", button_hover_color="#e1eafd", text_color=TEXT,
                                         width=135, values=("128 kbps", "192 kbps", "256 kbps"))
        self.bitrate_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 8))
        self.speed, self.pitch = tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)
        self.speed_label, self.pitch_label = tk.StringVar(), tk.StringVar()
        ttk.Label(self.advanced, textvariable=self.speed_label).grid(row=2, column=0, sticky="w")
        ttk.Label(self.advanced, textvariable=self.pitch_label).grid(row=2, column=1, sticky="w", padx=(8, 0))
        self.speed_scale = ctk.CTkSlider(self.advanced, from_=0.5, to=2.0, variable=self.speed, width=130,
                                        height=18, progress_color=ACCENT, button_color=ACCENT, fg_color="#e6ecf8")
        self.speed_scale.grid(row=3, column=0, sticky="ew", pady=5)
        self.pitch_scale = ctk.CTkSlider(self.advanced, from_=0.5, to=2.0, variable=self.pitch, width=130,
                                        height=18, progress_color=ACCENT, button_color=ACCENT, fg_color="#e6ecf8")
        self.pitch_scale.grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=5)
        self.reset_speed = StudioButton(self.advanced, text="Đặt lại tốc độ", command=lambda: self.speed.set(1.0))
        self.reset_speed.grid(row=4, column=0, sticky="ew")
        StudioButton(self.advanced, text="Đặt lại tất cả", command=self._reset_advanced).grid(row=4, column=1, sticky="ew", padx=(8, 0))
        self.output_group = ttk.Frame(right)
        self.output_group.pack(fill="x", pady=(10, 0))
        self.output_group.columnconfigure(1, weight=1)
        self.output_dir = tk.StringVar()
        ttk.Label(self.output_group, text="Nơi lưu").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(self.output_group, textvariable=self.output_dir, state="readonly", width=8).grid(row=0, column=1, sticky="ew")
        self.browse_button = StudioButton(self.output_group, text="Chọn…", width=5, command=self._browse_output_dir)
        self.browse_button.grid(row=0, column=2, padx=(4, 0))
        StudioButton(self.output_group, text="Mở", width=3, command=self._open_output_directory).grid(row=0, column=3, padx=(4, 0))
        ttk.Label(self.output_group, text="Tên file").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.filename = tk.StringVar()
        name_entry = ttk.Entry(self.output_group, textvariable=self.filename, width=10)
        name_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Label(self.output_group, text="Để trống tên file để tự đặt.", style="Muted.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.controls += [self.speed_scale, self.pitch_scale, name_entry, self.reset_speed]
        self.search.trace_add("write", self._filter_voices)
        self.region.trace_add("write", self._filter_voices)
        self.tts_provider.trace_add("write", self._provider_changed)
        self.language.trace_add('write', self._language_changed)
        self.model.trace_add('write', self._model_changed)
        for var in (self.voice, self.speed, self.pitch, self.bitrate, self.model, self.output_dir, self.filename):
            var.trace_add("write", self._config_changed)
        _, proxy_workspace = self._scrollable(self.proxy_tab)
        connection_card = Card(proxy_workspace, "Kết nối đang sử dụng")
        connection_card.pack(fill="x", pady=(0, 8))
        connection = connection_card.body
        self.connection_text = tk.StringVar()
        ttk.Label(connection, textvariable=self.connection_text, wraplength=760).pack(anchor="w")
        actions = ttk.Frame(connection)
        actions.pack(fill="x", pady=(6, 0))
        self.check_proxy_button = StudioButton(actions, text="Kiểm tra kết nối", width=140, command=lambda: self._quick_proxy(False))
        self.check_proxy_button.pack(side="left")
        self.rotate_proxy_button = StudioButton(actions, text="Xoay IP ngay", width=120, command=lambda: self._quick_proxy(True))
        self.rotate_proxy_button.pack(side="left", padx=6)
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.withdraw()
        self.settings_window.title("Cài đặt · Voice Pro - chất lượng cao nhất")
        self.settings_window.geometry("820x700")
        self.settings_window.minsize(700, 520)
        self.settings_window.transient(self.root)
        self.settings_window.protocol("WM_DELETE_WINDOW", self._close_settings)
        self.settings_window.bind("<Escape>", lambda _: self._close_settings())
        settings_outer = ttk.Frame(self.settings_window, padding=14)
        settings_outer.pack(fill="both", expand=True)
        settings_header = ttk.Frame(settings_outer)
        settings_header.pack(fill="x", pady=(0, 8))
        ttk.Label(settings_header, text="Cài đặt", style="Title.TLabel").pack(side="left")
        StudioButton(settings_header, text="Đóng", width=70, command=self._close_settings).pack(side="right")
        StudioButton(settings_header, text="Lưu cài đặt", style="Primary.TButton",
                   command=lambda: self.settings_panel.save()).pack(side="right", padx=6)
        StudioButton(settings_header, text="Hủy thay đổi", width=115, command=lambda: self.settings_panel.cancel()).pack(side="right")
        settings_canvas, preferences = self._scrollable(settings_outer)
        self.settings_panel = SettingsDialog(proxy_workspace, self, embedded=True, preferences_parent=preferences)
        self.settings_panel.pack(fill="x", pady=(12, 0))
        ttk.Label(settings_outer, textvariable=self.settings_panel.status, wraplength=740,
                  style="Muted.TLabel").pack(fill="x", pady=(0, 8), before=settings_canvas.master)
        self.license_panel = LicensePanel(preferences, self)
        self.license_panel.pack(fill="x", pady=(12, 0))
        self._build_traffic_panel(proxy_workspace)
        _, history_workspace = self._scrollable(self.history_tab)
        self._build_recent(history_workspace)
        activity = ttk.LabelFrame(history_workspace, text="Nhật ký hoạt động", padding=10)
        activity.pack(fill="x", pady=8)
        log_actions = ttk.Frame(activity)
        log_actions.pack(fill="x")
        ttk.Label(log_actions, text="Trạng thái tác vụ trực tiếp; nhật ký kỹ thuật đầy đủ ở mục Quản trị.").pack(side="left")
        StudioButton(log_actions, text="Xóa nhật ký hiển thị", width=160, command=self._clear_activity).pack(side="right")
        log_frame = ttk.Frame(activity)
        log_frame.pack(fill="x", pady=6)
        self.activity_log = tk.Text(log_frame, height=6, wrap="word", state="disabled", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, command=self.activity_log.yview)
        log_scroll.pack(side="right", fill="y")
        self.activity_log.configure(yscrollcommand=log_scroll.set)
        self.activity_log.pack(fill="x")
        self.status.trace_add("write", self._activity_changed)
        self._refresh_connection()
        self.root.bind("<MouseWheel>", self._scroll_workspace, add="+")
        self.settings_window.bind("<MouseWheel>", self._scroll_workspace, add="+")
        self.root.bind("<FocusIn>", self._reveal_focus, add="+")
        self.settings_window.bind("<FocusIn>", self._reveal_focus, add="+")

    def _scrollable(self, parent):
        frame = ttk.Frame(parent, style="Shell.TFrame")
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, bg=BACKGROUND, highlightthickness=0, width=1, height=1)
        scrollbar = ctk.CTkScrollbar(frame, command=canvas.yview, width=8, fg_color=BACKGROUND,
                                      button_color="#d7deec", button_hover_color="#b9c8e6")
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, style="Shell.TFrame")
        handle = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(handle, width=e.width))
        self._scroll_areas.append((canvas, body))
        return canvas, body

    def _scroll_workspace(self, event):
        widget = event.widget
        while widget is not None:
            if isinstance(widget, (tk.Text, ttk.Treeview, ttk.Combobox, ttk.Scale, ctk.CTkSlider, ctk.CTkComboBox)):
                return
            widget = getattr(widget, "master", None)
        if not event.delta:
            return
        for canvas, body in self._scroll_areas:
            if event.widget == canvas or str(event.widget).startswith(str(body) + ".") or event.widget == body:
                if body.winfo_height() > canvas.winfo_height():
                    canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

    def _update_navigation(self, _=None):
        for tab, button in self.nav_buttons.items():
            selected = tab == self.tabs.select()
            button.configure(fg_color=ACCENT if selected else BACKGROUND,
                             text_color="white" if selected else MUTED,
                             border_color=ACCENT if selected else BACKGROUND)

    def _build_bottom(self, outer):
        dock = ctk.CTkFrame(outer, fg_color="white", bg_color=BACKGROUND, corner_radius=14, border_width=1, border_color=BORDER)
        dock.pack(side="bottom", fill="x", pady=(12, 0))
        bottom = ttk.Frame(dock, padding=(12, 8))
        bottom.pack(fill="x", padx=4, pady=4)
        self.recovery = ttk.Frame(bottom)
        self.recovery.pack(fill="x")
        self.recovery_text = tk.StringVar()
        ttk.Label(self.recovery, textvariable=self.recovery_text, wraplength=680).pack(side="left")
        self.continue_button = StudioButton(self.recovery, text="Tiếp tục", command=self._continue)
        self.continue_button.pack(side="right", pady=(0, 8))
        self.status = tk.StringVar(value="Nhập nội dung để bắt đầu")
        line = ttk.Frame(bottom)
        self.task_row = line
        line.pack(fill="x")
        self.status_label = ttk.Label(line, textvariable=self.status, wraplength=300)
        self.status_label.pack(side="left")
        self.btn_start = StudioButton(line, text="Tạo giọng nói", width=160, style="Primary.TButton", command=self._start_generation)
        self.btn_start.pack(side="right", padx=(8, 0))
        self.btn_stop = StudioButton(line, text="Dừng", width=75, command=self._stop_generation, state="disabled")
        self.btn_stop.pack(side="right")
        self.workers = tk.IntVar(value=self.store.settings["concurrency"])
        self.workers_combo = ttk.Combobox(line, textvariable=self.workers, state="readonly",
                                         values=tuple(range(1, 9)), width=3, height=8)
        self.workers_combo.pack(side="right", padx=(4, 10))
        ttk.Label(line, text="Số luồng").pack(side="right")
        self.progress_bar = ctk.CTkProgressBar(bottom, height=4, corner_radius=2,
                                               fg_color="#edf1f9", progress_color=ACCENT)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(6, 6))
        self.error_actions = ttk.Frame(bottom)
        self.error_actions.pack(fill="x")
        self.retry_button = StudioButton(self.error_actions, text="Thử lại / Tiếp tục", width=145, command=self._continue)
        self.retry_button.pack(side="left")
        StudioButton(self.error_actions, text="Chọn nơi lưu khác…", width=155, command=self._browse_output_dir).pack(side="left", padx=8)
        StudioButton(self.error_actions, text="Quản lý proxy", width=125, command=self.open_proxy).pack(side="left")
        self.error_actions.pack_forget()
        self.result_text = tk.StringVar(value="Kết quả: Chưa có file")
        playerbar = ttk.Frame(bottom)
        playerbar.pack(fill="x")
        self.play_button = StudioButton(playerbar, text="Nghe", width=95, command=self._play_pause, state="disabled")
        self.play_button.pack(side="left")
        self.return_result_button = StudioButton(playerbar, text="Nghe kết quả", width=115, command=self._return_to_result)
        self.seek = ctk.CTkSlider(playerbar, from_=0, to=1, height=18, width=100,
                                   progress_color=ACCENT, button_color=ACCENT, fg_color="#e6ecf8")
        self.seek.set(0)
        self.seek.pack(side="left", fill="x", expand=True, padx=10)
        self.seek.bind("<ButtonPress-1>", lambda _: setattr(self, "seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._seek_released)
        self.seeking = False
        self.play_time = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(playerbar, textvariable=self.play_time).pack(side="left")
        self.open_button = StudioButton(playerbar, text="Mở vị trí file", width=115, command=self._open_result, state="disabled")
        self.open_button.pack(side="right", padx=(12, 0))
        self.external_button = StudioButton(bottom, text="Mở bằng ứng dụng mặc định", width=230, command=self._open_external)
        self.connection_summary = tk.StringVar()
        metadata = ttk.Frame(bottom)
        metadata.pack(fill="x", pady=(6, 0))
        metadata.columnconfigure(0, weight=1)
        ttk.Label(metadata, textvariable=self.result_text, style="Muted.TLabel", width=1).grid(row=0, column=0, sticky="ew")
        ttk.Label(metadata, textvariable=self.connection_summary, style="Muted.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))
    def _build_recent(self, parent):
        self.recent_toggle = ttk.Label(parent, text="Kết quả gần đây", style="Section.TLabel")
        self.recent_toggle.pack(anchor="w", pady=(12, 4))
        self.recent_frame = ttk.Frame(parent)
        self.recent_frame.pack(fill="x")
        self.recent_choice = tk.StringVar()
        self.recent_combo = ttk.Combobox(self.recent_frame, textvariable=self.recent_choice, state="readonly")
        self.recent_combo.pack(side="left", fill="x", expand=True)
        StudioButton(self.recent_frame, text="Chọn", width=75, command=self._select_recent).pack(side="left", padx=6)
        StudioButton(self.recent_frame, text="Bỏ khỏi danh sách", width=150, command=self._remove_recent).pack(side="left")

    def _build_traffic_panel(self, parent):
        card = Card(parent, "Lưu lượng đã dùng qua proxy")
        card.pack(fill="x", pady=(8, 0))
        panel = card.body
        self.traffic_total = tk.StringVar(value="Chưa có dữ liệu.")
        ttk.Label(panel, textvariable=self.traffic_total, style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Bộ đếm lưu byte gốc; KB dùng 1 KB = 1.024 byte. Mẫu nghe offline không dùng proxy.",
                  style="Muted.TLabel", wraplength=760).pack(anchor="w", pady=(4, 8))
        columns = ("proxy", "tx", "rx", "total", "requests", "method", "last")
        self.traffic_tree = ttk.Treeview(panel, columns=columns, show="headings", height=5)
        headings = {
            "proxy": "Proxy", "tx": "Gửi", "rx": "Nhận", "total": "Tổng",
            "requests": "Request", "method": "Phạm vi đo", "last": "Dùng gần nhất",
        }
        widths = {"proxy": 155, "tx": 105, "rx": 105, "total": 115,
                  "requests": 65, "method": 120, "last": 125}
        for column in columns:
            self.traffic_tree.heading(column, text=headings[column])
            self.traffic_tree.column(column, width=widths[column], anchor="w")
        self.traffic_tree.pack(fill="x", expand=True)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(8, 0))
        StudioButton(actions, text="Làm mới", command=self._refresh_traffic).pack(side="left")
        StudioButton(actions, text="Xuất CSV…", command=self._export_traffic).pack(side="left", padx=6)
        StudioButton(actions, text="Đặt lại bộ đếm", width=135, command=self._reset_traffic).pack(side="right")

    @staticmethod
    def _traffic_amount(value):
        value = max(0, int(value or 0))
        return f"{value:,} B ({value / 1024:.2f} KB)"

    def _traffic_rows(self):
        stored = {entry.get("proxy_id"): entry for entry in self.store.proxy_traffic_entries()}
        rows = []
        for item in self.proxy_catalog.get_all():
            entry = stored.pop(item.id, {})
            rows.append({
                "proxy_id": item.id, "name": item.name, "address": self._proxy_hint(item.address),
                "tx_bytes": entry.get("tx_bytes", 0), "rx_bytes": entry.get("rx_bytes", 0),
                "request_count": entry.get("request_count", 0), "measurement": entry.get("measurement", "chưa dùng"),
                "last_used_at": entry.get("last_used_at", 0),
            })
        rows.extend(stored.values())
        return rows

    @staticmethod
    def _proxy_hint(address):
        parsed = parse_proxy(address)
        return f"{parsed.host}:{parsed.port}" if parsed else ""

    def _refresh_traffic(self):
        if not hasattr(self, "traffic_tree"):
            return
        for child in self.traffic_tree.get_children():
            self.traffic_tree.delete(child)
        rows = self._traffic_rows()
        total = 0
        for entry in rows:
            tx = max(0, int(entry.get("tx_bytes", 0) or 0))
            rx = max(0, int(entry.get("rx_bytes", 0) or 0))
            total += tx + rx
            stamp = entry.get("last_used_at", 0)
            last = time.strftime("%d/%m/%Y %H:%M", time.localtime(stamp)) if stamp else "—"
            self.traffic_tree.insert("", "end", values=(
                f"{entry.get('name', 'Proxy')} · {entry.get('address', '')}".strip(" ·"),
                self._traffic_amount(tx), self._traffic_amount(rx), self._traffic_amount(tx + rx),
                f"{int(entry.get('request_count', 0) or 0):,}",
                entry.get("measurement", "unknown"), last,
            ))
        self.traffic_total.set(f"Tổng đã ghi: {total:,} B · {format_bytes(total)} qua {len(rows)} proxy")

    def _export_traffic(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Xuất lưu lượng proxy",
                                            defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(("proxy_id", "proxy", "host_port", "sent_bytes", "received_bytes",
                                 "total_bytes", "total_kb", "request_count", "measurement", "last_used"))
                for entry in self._traffic_rows():
                    tx = max(0, int(entry.get("tx_bytes", 0) or 0))
                    rx = max(0, int(entry.get("rx_bytes", 0) or 0))
                    stamp = entry.get("last_used_at", 0)
                    writer.writerow((entry.get("proxy_id", ""), entry.get("name", ""), entry.get("address", ""),
                                     tx, rx, tx + rx, f"{(tx + rx) / 1024:.2f}",
                                     int(entry.get("request_count", 0) or 0), entry.get("measurement", "unknown"),
                                     time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)) if stamp else ""))
            self.status.set("Đã xuất lưu lượng proxy.")
        except OSError:
            self.status.set("Không xuất được CSV. Hãy chọn vị trí khác.")

    def _reset_traffic(self):
        if self.busy:
            self.status.set("Hãy đợi tác vụ hiện tại kết thúc rồi đặt lại bộ đếm.")
            return
        if not messagebox.askyesno("Đặt lại bộ đếm", "Xóa toàn bộ số liệu lưu lượng proxy đã lưu?", parent=self.root):
            return
        try:
            self.store.reset_proxy_traffic()
            self._refresh_traffic()
            self.status.set("Đã đặt lại bộ đếm lưu lượng proxy.")
        except OSError:
            self.status.set("Không đặt lại được bộ đếm. Kiểm tra quyền ghi dữ liệu.")

    def _restore(self):
        s = self.store.settings
        provider, _ = split_voice_id(s['voice_id'])
        self.tts_provider.set(PRIMARY_PROVIDER_LABEL if provider != 'capcut' else 'CapCut')
        self._filter_voices()
        self._set_voice(s["voice_id"])
        for var, key in ((self.model, "model_id"), (self.speed, "speed"), (self.pitch, "pitch"),
                         (self.bitrate, "bitrate"), (self.output_dir, "output_dir")):
            var.set(model_label(s[key]) if key == 'model_id' and provider == 'everai' else s[key])
        if provider == 'capcut':
            self.pitch.set(1.0)
            self.bitrate.set('128 kbps')
            self.model.set('CapCut')
        self._refresh_languages()
        if s.get('tts_language') in self.language_combo.cget('values'):
            self.language.set(s['tts_language'])
        draft = self.store.load("draft.json", {})
        if isinstance(draft, dict):
            self.txt_input.insert("1.0", str(draft.get("text", "")))
            self.filename.set(str(draft.get("filename", "")))
            self.source_name = str(draft.get("source", ""))
        self._update_labels()
        self._update_stats()
        self._refresh_recent()
        self._refresh_traffic()
        self.refresh_recovery()
        if self.store.warnings:
            self.status.set(self.store.warnings[-1])
        elif self.txt_input.get("1.0", "end-1c").strip():
            self.status.set("Đã khôi phục bản nháp · Sẵn sàng")
        if self.store.recent:
            path = Path(self.store.recent[0]["path"])
            if path.is_file():
                self._load_result(path)

    def _provider_id(self):
        value = self.tts_provider.get()
        return 'everai' if value == PRIMARY_PROVIDER_LABEL else value.lower()

    def _set_voice(self, voice_id):
        available = [v for v in STUDIO_VOICES if v['engine'] == self._provider_id()]
        item = next((v for v in available if v["id"] == voice_id), available[0])
        self.voice.set(item["display"])

    def _provider_changed(self, *_):
        provider = self._provider_id()
        self.preview_description.set('Nghe mẫu gốc miễn phí.' if provider == 'everai'
                                     else 'Nghe thử tạo audio mới và sử dụng hạn mức.')
        self.region.set('Tất cả')
        available = [v for v in STUDIO_VOICES if v['engine'] == provider]
        self.region_combo.configure(values=['Tất cả'] + sorted({v['region'] for v in available}))
        self._filter_voices()
        for control in (self.model_combo, self.pitch_scale, self.bitrate_combo):
            control.configure(state=('readonly' if control is not self.pitch_scale else 'normal')
                              if provider == 'everai'
                              else 'disabled')
        self.model_combo.configure(values=[model_label('everai-v1.6'), model_label('everai-v1.5')]
                                   if provider == 'everai' else ['CapCut'])
        if provider != 'everai':
            self.pitch.set(1.0)
            self.bitrate.set('128 kbps')
            self.model.set('CapCut')
        elif self.model.get() not in (model_label('everai-v1.6'), model_label('everai-v1.5')):
            self.model.set(model_label('everai-v1.6'))
        self.language.set('Tất cả')
        self._refresh_languages()
        if not self.restoring:
            self._schedule_save()

    def _filter_voices(self, *_):
        query = normalized_search(self.search.get())
        voices = [v["display"] for v in STUDIO_VOICES
                  if v['engine'] == self._provider_id()
                  and (self.region.get() == "Tất cả" or v["region"] == self.region.get())
                  and (self.language.get() == 'Tất cả' or v['lang'] == language_code(self.language.get()))
                  and query in normalized_search(v["display"])]
        self.cbo_voice.configure(values=voices)
        if self.voice.get() not in voices:
            self.voice.set(voices[0] if voices else "")

    def _refresh_languages(self):
        provider = self._provider_id()
        codes = {v['lang'] for v in STUDIO_VOICES if v['engine'] == provider}
        values = ['Tất cả'] + sorted(language_label(code) for code in codes)
        self.language_combo.configure(values=values)
        if self.language.get() not in values:
            self.language.set('Tất cả')

    def _language_changed(self, *_):
        self._filter_voices()
        if not self.restoring:
            self._schedule_save()

    def _model_changed(self, *_):
        self._refresh_languages()

    def _snapshot(self):
        selected = next((v for v in STUDIO_VOICES if v["display"] == self.voice.get()), None)
        if selected is None:
            raise ValueError("Hãy chọn một giọng đọc.")
        model = self.model.get()
        if self._provider_id() == 'everai' and model.startswith(PRIMARY_MODEL_PREFIX):
            model = 'everai-' + model[len(PRIMARY_MODEL_PREFIX):]
        return dict(speaker_id=selected["id"], model_id=model,
                    speed=round(self.speed.get(), 1), pitch=round(self.pitch.get(), 1),
                    volume=100, bitrate=self.bitrate.get(), audio_type="mp3")

    def _config_changed(self, *_):
        self._update_labels()
        if not self.restoring:
            self._schedule_save()

    def _update_labels(self):
        self.speed_label.set(f"Tốc độ: {self.speed.get():.1f}x")
        self.pitch_label.set(f"Cao độ: {self.pitch.get():.1f}x")

    def _modified(self, _=None):
        if not self.txt_input.edit_modified():
            return
        self.txt_input.edit_modified(False)
        if not self.restoring:
            self._schedule_save()

    def _schedule_save(self):
        if self.save_id:
            self.root.after_cancel(self.save_id)
        self.save_id = self.root.after(500, self._save)

    def _save(self):
        if self.save_id:
            self.root.after_cancel(self.save_id)
        self.save_id = None
        try:
            if self.voice.get():
                config = self._snapshot()
                self.store.settings.update(voice_id=config["speaker_id"], model_id=config["model_id"],
                                           speed=config["speed"], pitch=config["pitch"], bitrate=config["bitrate"])
            self.store.settings["output_dir"] = self.output_dir.get()
            self.store.settings['tts_provider'] = self._provider_id()
            self.store.settings['tts_language'] = self.language.get()
            self.store.save_settings()
            atomic_json(self.store.root / "draft.json",
                        dict(text=self.txt_input.get("1.0", "end-1c"), filename=self.filename.get(), source=self.source_name))
            self._update_stats()
        except (OSError, ValueError, tk.TclError):
            self.status.set("Chưa lưu được bản nháp hoặc cấu hình. Kiểm tra nơi lưu dữ liệu.")

    def _update_stats(self):
        if hasattr(self, 'input_mode') and self.input_mode.get() == 'queue':
            items = self.txt_queue['items']
            config = self.txt_queue.get('config')
            provider = split_voice_id(config['speaker_id'])[0] if config else self._provider_id()
            self.stats.set(f"{len(items)} file · {sum(sum(map(len, speech_chunks(i['text'], provider))) for i in items):,} ký tự tính phí")
            if not self.busy:
                self.btn_start.configure(text=f'Tạo giọng · {len(items)} file', state='normal' if any(i['state'] not in ('done', 'skipped') for i in items) else 'disabled')
            return
        self.btn_start.configure(text='Tạo giọng nói')
        text = self.txt_input.get("1.0", "end-1c").strip()
        try:
            chunks = speech_chunks(text, self._provider_id())
        except ValueError:
            self.stats.set('SRT chưa hợp lệ: kiểm tra mốc thời gian và nội dung cue.')
            self.btn_start.configure(state='disabled')
            return
        self.stats.set(f"{sum(map(len,chunks)):,} ký tự tính phí · {len(text.split()):,} từ · {len(chunks)} đoạn")
        if not self.busy:
            self.btn_start.configure(state="normal" if text and self.voice.get() else "disabled")
            if self.status.get() in ("Nhập nội dung để bắt đầu", "Sẵn sàng"):
                self.status.set("Sẵn sàng" if text else "Nhập nội dung để bắt đầu")

    def _reset_advanced(self):
        self.speed.set(1.0)
        self.pitch.set(1.0)
        self.bitrate.set("128 kbps")
        self.model.set(model_label("everai-v1.6"))

    def _refresh_recent(self):
        self.recent_combo["values"] = [f"{entry.get('time', '')} · {Path(entry['path']).name}" for entry in self.store.recent]
        if self.store.recent:
            self.recent_combo.current(0)

    def _select_recent(self):
        index = self.recent_combo.current()
        if 0 <= index < len(self.store.recent):
            path = Path(self.store.recent[index]["path"])
            if path.is_file():
                self._load_result(path)
            else:
                self.status.set("Không còn file ở vị trí cũ. Bạn có thể bỏ mục này khỏi danh sách.")

    def _remove_recent(self):
        index = self.recent_combo.current()
        if 0 <= index < len(self.store.recent):
            try:
                self.store.remove_result(self.store.recent[index]["path"])
            except OSError:
                self.status.set("Chưa lưu được danh sách gần đây.")
                return
            self.recent_choice.set("")
            self._refresh_recent()

    def _clear_text(self):
        if not self.busy:
            self.txt_input.edit_separator()
            self.txt_input.delete("1.0", "end")
            self.txt_input.edit_separator()
            self.source_name = ""
            self.status.set("Đã xóa nội dung. Ctrl+Z để hoàn tác.")

    def _load_file(self, kind=None):
        if self.busy:
            return
        if kind != 'srt':
            self._queue_import()
            return
        self.input_mode.set('text')
        self._queue_refresh()
        self.tabs.select(self.generation_tab)
        filetypes = [("Phụ đề SRT", "*.srt")] if kind == "srt" else [("Văn bản TXT", "*.txt")] if kind == "txt" else [("Văn bản và phụ đề", "*.txt *.srt")]
        path = filedialog.askopenfilename(parent=self.root, filetypes=filetypes + [("Mọi file", "*.*")])
        if not path:
            return
        try:
            is_srt = Path(path).suffix.lower() == ".srt"
            preserve_timing = is_srt and self._provider_id() != 'everai'
            text = (Path(path).read_text(encoding='utf-8-sig') if preserve_timing else
                    "\n".join(item["text"] for item in parse_srt_file(path)) if is_srt
                    else Path(path).read_text(encoding="utf-8-sig"))
            if not text.strip():
                raise ValueError()
            append = False
            if self.txt_input.get("1.0", "end-1c").strip():
                replace = messagebox.askyesnocancel("Nạp nội dung", "Thay thế nội dung hiện tại?\nCó: thay thế · Không: nối thêm · Hủy: giữ nguyên", parent=self.root)
                if replace is None:
                    return
                append = not replace
            self.txt_input.edit_separator()
            if not append:
                self.txt_input.delete("1.0", "end")
            self.txt_input.insert("end", ("\n" if append else "") + text)
            self.txt_input.edit_separator()
            self.source_name = Path(path).stem
            self.status.set('Đã nạp SRT; audio sẽ giữ mốc thời gian từng cue.' if preserve_timing else
                            "Đã lấy lời thoại từ phụ đề; chưa đồng bộ theo mốc thời gian." if is_srt else "Đã nạp nội dung.")
            self._schedule_save()
        except (OSError, UnicodeError, ValueError):
            self.status.set("Không đọc được nội dung. Hãy chọn file TXT UTF-8 hoặc SRT hợp lệ.")

    def _browse_output_dir(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, initialdir=self.output_dir.get() if Path(self.output_dir.get()).is_dir() else None)
        if path:
            self.output_dir.set(path)
            self.status.set("Đã đổi nơi lưu. Bạn có thể tiếp tục tác vụ dở.")

    def refresh_recovery(self):
        jobs = self.store.unfinished_jobs()
        items = self.txt_queue['items']
        ignored = {i.get('job') for i in items if i['state'] in ('done', 'skipped')}
        jobs = [(path, manifest) for path, manifest in jobs if path.name not in ignored]
        remaining = [i for i in items if i['state'] not in ('done', 'skipped')]
        if self.input_mode.get() == 'queue' and remaining:
            eligible = {i.get('job') for i in remaining}
            jobs = [(path, manifest) for path, manifest in jobs if path.name in eligible]
        self.pending_job = jobs[0][0] if jobs else None
        if jobs:
            manifest = jobs[0][1]
            self.recovery_text.set(f"Tác vụ chưa hoàn tất · đã giữ {len(manifest.get('completed', {}))} đoạn")
            if not self.recovery.winfo_manager():
                self.recovery.pack(fill="x", before=self.task_row)
            self.continue_button.configure(state="disabled" if self.busy else "normal")
        else:
            self.recovery.pack_forget()

    def _continue(self):
        if self.busy:
            return
        if self.input_mode.get() == 'queue' and any(i['state'] not in ('done', 'skipped')
                                                    for i in self.txt_queue['items']):
            self._queue_start()
            return
        if not self.pending_job:
            self._start_generation()
            return
        try:
            manifest = read_json(self.pending_job / 'manifest.json')
            config = manifest['config']
            text = manifest['text']
            current_text = self.txt_input.get('1.0', 'end-1c').strip()
            try:
                current_config = self._snapshot()
            except ValueError:
                current_config = None
            if current_text != text or current_config != config:
                if not messagebox.askyesno('Phục hồi tác vụ',
                        'Nạp lại nội dung và cấu hình của tác vụ dở? Nội dung đang soạn sẽ được thay thế.',
                        parent=self.root):
                    return
                provider, _ = split_voice_id(config['speaker_id'])
                self.search.set('')
                self.tts_provider.set('CapCut' if provider == 'capcut' else PRIMARY_PROVIDER_LABEL)
                self._provider_changed()
                self._set_voice(config['speaker_id'])
                model, _, language = config['model_id'].partition('@')
                self.model.set(model_label(model) if provider == 'everai' else model)
                self._refresh_languages()
                self.language.set(language_label(language) if language else 'Tất cả')
                for key in ('speed', 'pitch', 'bitrate'):
                    getattr(self, key).set(config[key])
                self.txt_input.delete('1.0', 'end')
                self.txt_input.insert('1.0', text)
                self.filename.set(Path(manifest['output_file']).name)
            self.input_mode.set('text')
            if not self._queue_refresh():
                return
        except (OSError, ValueError, KeyError, TypeError):
            self._show_error('Không đọc được tác vụ dở; dữ liệu được giữ nguyên.', 'recovery')
            return
        self._start_generation(resume=True)

    def _new_engine(self, provider=None):
        if self.license_manager is None:
            raise LicenseError(self.license_error)
        if self.network_busy or self.settings_panel.busy:
            raise ValueError("Chờ kiểm tra / đổi IP hoàn tất trước khi tạo giọng.")
        if not self.settings_panel.save():
            self.open_proxy()
            raise ValueError("Kiểm tra cấu hình kết nối và bấm Lưu cài đặt.")
        s = self.store.settings
        provider = provider or self._provider_id()
        if provider != 'capcut' and s["use_proxy"] and not self.proxy_catalog.get_active_proxy():
            raise ValueError("Chưa có proxy hợp lệ được kích hoạt. Thêm và kích hoạt proxy trong tab Quản lý proxy.")
        engine_class = EverAITTSEngine if provider == 'everai' else MultiProviderTTSEngine
        if provider != 'everai':
            TTSService().preflight(provider)
        return engine_class(proxy_catalog=None if provider == 'capcut' else self.proxy_catalog,
                               session_id=self.session_id,
                               license_manager=self.license_manager,
                               randomize_device=s["randomize_device"],
                               download_via_direct_ip=s["download_via_direct_ip"],
                               concurrency=s["concurrency"], log_callback=self.log,
                               temp_dir=self.store.root / "samples" / "chunks",
                               progress_callback=lambda c, t, p, txt: self.ui_queue.put(("progress", p)),
                               status_callback=lambda kind, details: self.ui_queue.put(("status", (kind, details))))

    def _start_generation(self, resume=False):
        if self.busy:
            return
        if self.input_mode.get() == 'queue':
            self._queue_start()
            return
        self.tabs.select(self.generation_tab)
        try:
            text = self.txt_input.get("1.0", "end-1c").strip()
            if not text:
                self.txt_input.focus_set()
                raise ValueError("Nhập nội dung để bắt đầu.")
            try:
                config = self._snapshot()
            except ValueError:
                self.cbo_voice.focus_set()
                raise
            if not ensure_ffmpeg():
                raise ValueError("Thiếu FFmpeg. Hãy dùng bản phát hành đầy đủ hoặc cài FFmpeg.")
            try:
                output = writable_directory(Path(self.output_dir.get()))
            except OSError:
                self.browse_button.focus_set()
                raise
            name = audio_filename(self.filename.get() or self.source_name or " ".join(text.split()[:8]))
            job = self.pending_job if resume else self.store.root / "jobs" / uuid.uuid4().hex
            if resume:
                manifest = read_json(job / "manifest.json")
                if manifest["text"] != text or manifest["config"] != config:
                    raise ValueError("Nội dung hoặc giọng đã đổi. Bấm Tạo giọng nói để bắt đầu bài mới.")
                if not self.filename.get():
                    name = Path(manifest["output_file"]).name
            self.current_engine = self._new_engine()
            self._save()
            self._run(text, output / name, config, job, preview=False)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._show_error(str(error) if isinstance(error, ValueError) else "Không ghi được vào thư mục. Hãy chọn nơi lưu khác.",
                             "preflight", retry=lambda: self._start_generation(resume=resume))

    def _preview(self):
        if self.busy:
            return
        selected = next((v for v in STUDIO_VOICES if v["display"] == self.voice.get()), None)
        if not selected:
            self.status.set("Hãy chọn một giọng để nghe mẫu.")
            return
        if selected['engine'] != 'everai':
            self._preview_configured()
            return
        sample = voice_demo_path(selected["id"])
        if not sample:
            self.status.set("Mẫu giọng chưa có hoặc bị hỏng. Hãy dùng bản ứng dụng đầy đủ bộ mẫu.")
            return
        self._load_playback(sample, "Mẫu gốc · " + selected["name"])
        if not self.player.last_error:
            self._play_pause()
            self.status.set("Đang nghe mẫu gốc có sẵn · " + selected["name"])

    def _preview_configured(self):
        if self.busy:
            return
        try:
            config = self._snapshot()
            digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode() + SAMPLE_TEXT.encode()).hexdigest()
            cache = writable_directory(self.store.root / "samples")
            sample = cache / (digest + ".mp3")
            if sample.is_file() and sample.stat().st_size:
                self._load_playback(sample, "Nghe thử giọng")
                self._play_pause()
                return
            if not ensure_ffmpeg():
                raise ValueError("Thiếu FFmpeg. Hãy dùng bản phát hành đầy đủ.")
            # Bound cached samples; the current playback file is never removed.
            for old in sorted(cache.glob("*.mp3"), key=lambda p: p.stat().st_mtime)[:-19]:
                if old != self.playback_file:
                    old.unlink(missing_ok=True)
            self.current_engine = self._new_engine()
            preview_root = cache / "jobs" / digest
            pending = [path for path in preview_root.glob("*/manifest.json")
                       if read_json(path, {}).get("status") != "completed"]
            preview_job = max(pending, key=lambda path: path.stat().st_mtime).parent if pending else preview_root / uuid.uuid4().hex
            self._run(SAMPLE_TEXT, sample, config, preview_job, preview=True)
        except (OSError, ValueError) as error:
            self._show_error(str(error) if isinstance(error, ValueError) else "Không lưu được mẫu nghe thử.", "preview")

    def _run(self, text, destination, config, job, preview):
        self.busy = "preview" if preview else "generation"
        self.error_actions.pack_forget()
        self.status_label.configure(style="TLabel")
        self.status.set("Đang tải mẫu nghe thử…" if preview else "Đang chuẩn bị…")
        self.progress_bar.set(0)
        self._set_busy(True)
        engine = self.current_engine
        self.outcome = None
        if job and not preview:
            self.pending_job = job
        self.proxy_catalog.suspended = True
        def worker():
            try:
                path = engine.process(text, destination, **config, job_dir=job,
                                      keep_temp_chunks=False if preview else True)
                outcome = (path, None, job, preview)
            except Exception as error:
                outcome = (None, error, job, preview)
            self.outcome = outcome
        self.thread = threading.Thread(target=worker, daemon=False)
        self.thread.start()

    def _set_busy(self, busy):
        for control in getattr(self, 'queue_controls', []):
            control.configure(state='disabled' if busy else 'normal')
        self.settings_panel.set_locked(bool(busy))
        for control in self.controls + [self.settings_button, self.import_button, self.txt_button, self.srt_button, self.clear_button, self.preview_button, self.browse_button]:
            control.configure(state="disabled" if busy else "normal")
        for combo in (self.cbo_voice, self.provider_combo, self.language_combo, self.region_combo, self.bitrate_combo, self.model_combo, self.workers_combo):
            combo.configure(state="disabled" if busy else "readonly")
        self.txt_input.configure(state="disabled" if busy else "normal")
        self.btn_start.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")
        self.continue_button.configure(state="disabled" if busy else "normal")
        self.license_panel.refresh(force=True)
        if not busy:
            if self._provider_id() != 'everai':
                for control in (self.pitch_scale, self.bitrate_combo):
                    control.configure(state='disabled')
                self.model_combo.configure(state='disabled')
            self._update_stats()
        self._refresh_connection()

    def _stop_generation(self):
        self.queue_running = False
        if self.current_engine and self.busy:
            self.current_engine.cancel()
            self.status.set("Đang dừng · các đoạn đã tạo được giữ lại")
            self.btn_stop.configure(state="disabled")

    def _handle_status(self, kind, details):
        if self.busy == "preview" or (self.current_engine and self.current_engine.is_cancelled):
            return
        if kind == "generating":
            self.status.set(f"Đã tạo {details['completed']}/{details['total']} đoạn")
        elif kind == "retrying":
            self.status.set("Đang thử kết nối lại…")
        elif kind == "merging":
            self.status.set("Đang ghép âm thanh…")
        if getattr(self, 'queue_current', None):
            self.status.set(self._queue_prefix() + self.status.get())

    def _poll(self):
        for _ in range(150):
            try:
                kind, value = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log_buffer.append(value)
            elif kind == "progress":
                self.progress_bar.set(value / 100)
            elif kind == "status":
                self._handle_status(*value)
            elif kind == "proxy_result":
                item, result = value
                current = self.proxy_catalog.get_by_id(item.id)
                if current:
                    for key in ("current_ip", "latency", "status", "last_rotate_at", "next_rotate_at", "error_message"):
                        setattr(current, key, getattr(item, key))
                    self.proxy_catalog.save()
                self.network_busy = False
                self.proxy_catalog.suspended = bool(self.busy)
                self.settings_panel.set_locked(bool(self.busy))
                self.status.set("Kết nối proxy tốt." if result.get("ok") else "Không kết nối được proxy. Kiểm tra cấu hình / nhà cung cấp.")
        if self.thread and not self.thread.is_alive() and self.outcome:
            outcome = self.outcome
            self.outcome = None
            self.thread = None
            self._finish(*outcome)
        self._update_player()
        self.license_panel.refresh()
        self._refresh_connection()
        if self.closing and not self.busy:
            self._destroy()
            return
        self.poll_id = self.root.after(80, self._poll)

    def _finish(self, path, error, job, preview):
        engine = self.current_engine
        if engine and hasattr(engine, "get_proxy_traffic_stats"):
            try:
                for entry in engine.get_proxy_traffic_stats():
                    self.store.record_proxy_traffic(entry.get("proxy_id"), entry.get("name"), entry.get("address"), {
                        "proxy_tx_bytes": entry.get("tx_bytes", 0),
                        "proxy_rx_bytes": entry.get("rx_bytes", 0),
                        "proxy_request_count": entry.get("request_count", 0),
                        "measurement": entry.get("measurement", "unknown"),
                    })
                self._refresh_traffic()
            except (OSError, TypeError, ValueError):
                self.log("Không lưu được bộ đếm lưu lượng proxy.", "WARNING")
        self.current_engine = None
        self.busy = None
        self.proxy_catalog.suspended = False
        self._set_busy(False)
        self.refresh_recovery()
        if error:
            self.log(f"Tác vụ kết thúc: {type(error).__name__}", "WARNING")
            retry = self._preview_configured if preview else self._continue
            if isinstance(error, EverAICancelled):
                self.status.set("Đã dừng. Các đoạn đã hoàn thành được giữ lại.")
            elif isinstance(error, LicenseError):
                self._show_error(str(error), "license", retry=retry)
            elif isinstance(error, OSError):
                self._show_error("Không lưu được file. Chọn nơi lưu khác rồi tiếp tục.", "storage", retry=retry)
            elif isinstance(error, AudioAssemblyError):
                self._show_error("Không ghép được âm thanh. Chọn nơi lưu khác hoặc dùng bản ứng dụng có FFmpeg đầy đủ.", "audio", retry=retry)
            elif isinstance(error, EverAIQuotaError):
                self._show_error("Dịch vụ đang giới hạn chuyển đổi. Kiểm tra tài khoản hoặc thử lại sau.", "quota", retry=retry)
            else:
                self._show_error("Chưa tạo xong âm thanh. Phần đã tạo được giữ lại; kiểm tra kết nối rồi thử lại.", "connection", retry=retry)
        elif preview:
            self.status.set("Mẫu nghe thử đã sẵn sàng.")
            self._load_playback(path, "Nghe thử giọng")
            self._play_pause()
        else:
            self.last_output_file = Path(path)
            try:
                self.store.add_result(path)
                self._refresh_recent()
            except OSError:
                self.log("Không lưu được lịch sử kết quả.", "WARNING")
            self._load_result(path)
            self.status.set(f"Đã lưu {Path(path).name}")
            self.progress_bar.set(1)
        if getattr(self, 'queue_current', None) is not None and not preview:
            item = self.queue_current
            item['state'] = 'error' if error else 'done'
            if path:
                item['result'] = str(path)
            self.queue_current = None
            self._queue_refresh()
            if error or self.closing:
                self.queue_running = False
            elif self.queue_running:
                self._queue_next()

    def _queue_refresh(self):
        labels = dict(wait='Chờ', running='Đang chạy', done='Hoàn tất', error='Đã dừng / lỗi', skipped='Bỏ qua')
        self.queue_tree.delete(*self.queue_tree.get_children())
        for index, item in enumerate(self.txt_queue['items']):
            self.queue_tree.insert('', 'end', iid=item['id'], values=(index+1, item['name'], len(item['text']), labels.get(item['state'], 'Chờ')))
        if self.input_mode.get() == 'queue':
            self.queue_panel.grid()
            self.editor_frame.grid_remove()
            self.queue_panel.master.rowconfigure(2, weight=1)
            self.queue_panel.master.rowconfigure(3, weight=0)
        else:
            self.queue_panel.grid_remove()
            self.editor_frame.grid()
            self.queue_panel.master.rowconfigure(2, weight=0)
            self.queue_panel.master.rowconfigure(3, weight=1)
        config = self.txt_queue.get('config')
        if config:
            voice = next((v['display'] for v in STUDIO_VOICES if v['id'] == config['speaker_id']), config['speaker_id'])
            self.queue_summary.set(f"Cấu hình đã chốt: {voice} · {config['model_id']} · tốc độ {config['speed']} · Xuất: {self.output_dir.get()}")
        else:
            self.queue_summary.set('Bấm Tạo giọng để áp dụng giọng và thông số hiện tại cho danh sách.')
        try:
            atomic_json(self.store.root / 'txt_queue.json', self.txt_queue)
        except OSError:
            self.queue_running = False
            self.status.set('Không lưu được hàng đợi. Kiểm tra dung lượng và quyền ghi trước khi tiếp tục.')
            return False
        self._update_stats()
        self.refresh_recovery()
        return True

    def _queue_prefix(self):
        item = self.queue_current
        return f"File {self.txt_queue['items'].index(item)+1}/{len(self.txt_queue['items'])} · {item['name']} · "

    def _queue_move(self, delta):
        if self.busy or self.queue_running:
            return
        selected = self.queue_tree.selection()
        items = self.txt_queue['items']
        if selected:
            index = next(i for i, item in enumerate(items) if item['id'] == selected[0])
            target = index + delta
            if 0 <= target < len(items):
                items[index], items[target] = items[target], items[index]
                self._queue_refresh()
                self.queue_tree.selection_set(selected[0])

    def _queue_remove(self):
        if not self.busy and not self.queue_running:
            selected = self.queue_tree.selection()
            self.txt_queue['items'] = [i for i in self.txt_queue['items'] if i['id'] not in selected]
            self._queue_refresh()

    def _queue_skip(self):
        if not self.busy:
            for item in self.txt_queue['items']:
                if item['id'] in self.queue_tree.selection() and item['state'] not in ('done', 'skipped'):
                    item['state'] = 'skipped'
            self._queue_refresh()

    def _queue_import(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, filetypes=[('Văn bản TXT', '*.txt')])
        if not paths:
            return
        pending = []
        for path in sorted(paths, key=lambda p: [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', Path(p).name)]):
            try:
                text = Path(path).read_text(encoding='utf-8-sig').strip()
                problem = '' if text else 'File trống'
            except (OSError, UnicodeError):
                text, problem = '', 'Không đọc được UTF-8'
            pending.append(dict(id=uuid.uuid4().hex, name=Path(path).name, text=text, state='wait', problem=problem))
        dialog = tk.Toplevel(self.root)
        dialog.title('Duyệt thứ tự file TXT')
        dialog.geometry('700x420')
        dialog.transient(self.root)
        dialog.grab_set()
        tree = ttk.Treeview(dialog, columns=('order', 'name', 'chars', 'check'), show='headings')
        for key, label in [('order', 'STT'), ('name', 'Tên file'), ('chars', 'Ký tự'), ('check', 'Kiểm tra')]:
            tree.heading(key, text=label)
            tree.column(key, width=140)
        tree.pack(fill='both', expand=True, padx=12, pady=12)
        scroll = ttk.Scrollbar(dialog, orient='vertical', command=tree.yview)
        scroll.pack(side='right', fill='y', before=tree)
        tree.configure(yscrollcommand=scroll.set)
        def refresh():
            tree.delete(*tree.get_children())
            for n, item in enumerate(pending):
                tree.insert('', 'end', iid=item['id'], values=(n+1, item['name'], len(item['text']), item['problem'] or 'Hợp lệ'))
        def move(delta):
            selection = tree.selection()
            if selection:
                n = next(n for n, i in enumerate(pending) if i['id'] == selection[0])
                if 0 <= n+delta < len(pending):
                    pending[n], pending[n+delta] = pending[n+delta], pending[n]
                    refresh()
                    tree.selection_set(selection[0])
        def remove():
            pending[:] = [i for i in pending if i['id'] not in tree.selection()]
            refresh()
        def sort_names():
            pending.sort(key=lambda i: [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', i['name'])])
            refresh()
        def add_more():
            extra = filedialog.askopenfilenames(parent=dialog, filetypes=[('Văn bản TXT', '*.txt')])
            for path in extra:
                try:
                    text = Path(path).read_text(encoding='utf-8-sig').strip()
                    problem = '' if text else 'File trống'
                except (OSError, UnicodeError):
                    text, problem = '', 'Không đọc được UTF-8'
                pending.append(dict(id=uuid.uuid4().hex, name=Path(path).name, text=text, state='wait', problem=problem))
            refresh()
        def accept():
            if not pending or any(i['problem'] for i in pending):
                messagebox.showerror('Kiểm tra file', 'Bỏ file trống/lỗi trước khi xác nhận.', parent=dialog)
                return
            self.txt_queue['items'].extend(pending)
            self.input_mode.set('queue')
            self._queue_refresh()
            dialog.destroy()
        buttons = ttk.Frame(dialog)
        buttons.pack(fill='x', padx=12, pady=12)
        for label, callback in [('↑', lambda: move(-1)), ('↓', lambda: move(1)), ('Bỏ file', remove), ('Thêm file', add_more), ('Theo tên', sort_names), ('Đưa vào hàng đợi', accept), ('Hủy', dialog.destroy)]:
            ttk.Button(buttons, text=label, command=callback).pack(side='left', padx=4)
        refresh()

    def _queue_start(self):
        if self.busy or self.closing:
            return
        try:
            remaining = [i for i in self.txt_queue['items'] if i['state'] not in ('done', 'skipped')]
            if not remaining:
                return
            if not any(i.get('job') for i in remaining):
                self.txt_queue['config'] = self._snapshot()
            self._save()
            self.queue_running = True
            self._queue_next()
        except (ValueError, OSError) as error:
            self.queue_running = False
            self._show_error(str(error), 'preflight', retry=self._queue_start)

    def _queue_next(self):
        item = next((i for i in self.txt_queue['items'] if i['state'] not in ('done', 'skipped')), None)
        if item is None:
            self.queue_running = False
            self.status.set('Đã hoàn tất hàng đợi TXT.')
            return
        try:
            if not ensure_ffmpeg():
                raise ValueError('Thiếu FFmpeg.')
            output = writable_directory(Path(self.output_dir.get()))
            provider, _ = split_voice_id(self.txt_queue['config']['speaker_id'])
            self.current_engine = self._new_engine(provider=provider)
            item.setdefault('job', uuid.uuid4().hex)
            item['state'] = 'running'
            self.queue_current = item
            if not self._queue_refresh():
                raise OSError('Không lưu được hàng đợi; chưa bắt đầu tạo giọng.')
            self._run(item['text'], output / audio_filename(Path(item['name']).stem), self.txt_queue['config'], self.store.root / 'jobs' / item['job'], preview=False)
            self.status.set(f"File {self.txt_queue['items'].index(item)+1}/{len(self.txt_queue['items'])}: {item['name']}")
        except (ValueError, OSError) as error:
            item['state'] = 'error'
            self.queue_current = None
            self.queue_running = False
            self._queue_refresh()
            self._show_error(str(error), 'preflight', retry=self._queue_start)

    def _show_error(self, text, code, retry=None):
        self.last_error_code = code
        self.status.set(text)
        self.status_label.configure(style="Error.TLabel")
        self.error_actions.pack(fill="x", after=self.progress_bar)
        self.retry_button.configure(command=retry or (self._preview_configured if code == "preview" else self._continue))

    def _load_result(self, path):
        self.last_output_file = Path(path)
        self.result_text.set("Kết quả: " + Path(path).name)
        self.open_button.configure(state="normal")
        self._load_playback(path, Path(path).name)

    def _load_playback(self, path, label):
        self.playback_file = Path(path)
        self.return_result_button.pack_forget()
        if self.last_output_file and self.playback_file != self.last_output_file:
            self.return_result_button.pack(side="left", after=self.play_button, padx=(6, 0))
        self.external_button.pack_forget()
        if self.player.load(path):
            duration = self.player.get_duration_ms()
            self.seek.configure(to=max(1, duration))
            self.play_button.configure(state="normal", text="Nghe")
            self.play_time.set("00:00 / " + self._clock(duration))
        else:
            self.play_button.configure(state="disabled")
            self.external_button.configure(text="Không phát được " + label + " · Mở bằng ứng dụng mặc định")
            self.external_button.pack(anchor="w")

    def _return_to_result(self):
        if self.last_output_file and self.last_output_file.is_file():
            self._load_result(self.last_output_file)
            if not self.player.last_error:
                self._play_pause()
        else:
            self.status.set("Không còn file kết quả ở vị trí cũ.")

    @staticmethod
    def _clock(ms):
        seconds = max(0, int(ms)) // 1000
        return f"{seconds // 60:02}:{seconds % 60:02}"

    def _play_pause(self):
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
        if self.player.last_error:
            self.status.set("Không phát được. Bạn có thể mở bằng ứng dụng mặc định.")
            self.external_button.pack(anchor="w")
        self._update_player()

    def _update_player(self):
        if self.playback_file:
            playing = self.player.is_playing()
            is_sample = self.playback_file != self.last_output_file
            self.play_button.configure(text="Tạm dừng" if playing else "Tiếp tục" if self.player._is_paused else "Nghe mẫu" if is_sample else "Nghe")
            duration = self.player.get_duration_ms()
            position = self.player.get_position_ms()
            if not self.seeking:
                self.seek.set(position)
            self.play_time.set(self._clock(position) + " / " + self._clock(duration))

    def _seek_released(self, _):
        self.seeking = False
        self.player.seek(int(float(self.seek.get())))

    def _open_result(self):
        if self.last_output_file and self.last_output_file.is_file():
            try:
                subprocess.Popen(["explorer.exe", "/select,", str(self.last_output_file)])
            except OSError:
                self.status.set("Không mở được thư mục. File vẫn được lưu tại " + str(self.last_output_file))
        else:
            self.status.set("Không còn file ở vị trí cũ.")

    def _open_external(self):
        if self.playback_file and self.playback_file.is_file():
            try:
                os.startfile(str(self.playback_file))
            except OSError:
                self.status.set("Không mở được ứng dụng phát audio mặc định.")

    def _open_output_directory(self):
        try:
            folder = writable_directory(Path(self.output_dir.get()))
            os.startfile(str(folder))
        except OSError:
            self.status.set("Không mở được thư mục xuất. Kiểm tra đường dẫn và quyền ghi.")

    def open_settings(self):
        self.settings_window.deiconify()
        self.settings_window.lift()
        self.settings_window.focus_set()

    def _close_settings(self):
        self.settings_window.withdraw()

    def open_license(self):
        self.open_settings()
        self.settings_window.update_idletasks()
        self.license_panel.import_button.focus_set()

    def open_proxy(self):
        self.tabs.select(self.proxy_tab)

    def _reveal_focus(self, event):
        widget = event.widget
        for canvas, body in self._scroll_areas:
            if not str(widget).startswith(str(body) + "."):
                continue
            top = widget.winfo_rooty() - canvas.winfo_rooty()
            if top < 0 or top + widget.winfo_height() > canvas.winfo_height():
                y = widget.winfo_rooty() - body.winfo_rooty()
                canvas.yview_moveto(max(0, y - 20) / max(1, body.winfo_height()))
            break

    def _clear_activity(self):
        self.activity_log.configure(state="normal")
        self.activity_log.delete("1.0", "end")
        self.activity_log.configure(state="disabled")

    def _activity_changed(self, *_):
        self.activity_log.configure(state="normal")
        self.activity_log.insert("end", time.strftime("%H:%M:%S ") + redact_log(self.status.get()) + "\n")
        if int(self.activity_log.index("end-1c").split(".")[0]) > 300:
            self.activity_log.delete("1.0", "2.0")
        self.activity_log.see("end")
        self.activity_log.configure(state="disabled")

    def _refresh_connection(self):
        item = self.proxy_catalog.get_active_proxy()
        if item and item.id == 'builtin-default':
            item = None
        use_proxy = True
        if item:
            parsed = parse_proxy(item.address)
            address = ("Ẩn" if item.id == "builtin-default" else
                       f"{parsed.host}:{parsed.port}" if parsed else "Địa chỉ chưa hợp lệ")
            route = "Đang dùng" if use_proxy else "Đã chọn · chưa bật proxy"
            self.connection_text.set(f"{route}: {item.name} · {address}\nIP: {item.current_ip or 'Chưa kiểm tra'} · {item.status} · {item.latency if item.latency is not None else '—'} ms")
            self.connection_summary.set(f"Proxy: {item.name} · {item.status}" if use_proxy else "Kết nối trực tiếp · Proxy đang tắt")
        else:
            self.connection_text.set("Chưa cấu hình proxy · đang dùng kết nối trực tiếp" if not use_proxy else "Đã bật proxy nhưng chưa có proxy. Thêm proxy trong tab Quản lý proxy.")
            self.connection_summary.set("Kết nối trực tiếp" if not use_proxy else "Chưa có proxy · Mở tab Quản lý proxy")
        if self._provider_id() == 'capcut':
            self.connection_summary.set('CapCut · Kết nối trực tiếp')
        disabled = bool(self.busy or self.network_busy or self.settings_panel.busy)
        self.check_proxy_button.configure(state="disabled" if disabled or not item else "normal")
        self.rotate_proxy_button.configure(state="disabled" if disabled or not item or not item.rotate_url else "normal")
        if time.monotonic() >= getattr(self, "next_proxy_refresh", 0):
            self.next_proxy_refresh = time.monotonic() + 1
            changed = False
            if self.settings_panel.busy:
                return
            if not hasattr(self, "proxy_health_seen"):
                self.proxy_health_seen = {}
            for staged in self.settings_panel.items:
                current = self.proxy_catalog.get_by_id(staged.id)
                keys = ("current_ip", "latency", "status", "last_rotate_at", "next_rotate_at", "error_message")
                if not self.settings_panel.activation_changed:
                    keys += ("is_active",)
                if current and current.address == staged.address and current.rotate_url == staged.rotate_url:
                    health = tuple(getattr(current, key) for key in keys)
                    if self.proxy_health_seen.get(staged.id) != health:
                        self.proxy_health_seen[staged.id] = health
                        for key, value in zip(keys, health):
                            setattr(staged, key, value)
                        changed = True
            if changed:
                self.settings_panel.refresh()

    def _quick_proxy(self, rotate):
        if self.busy or self.network_busy or self.settings_panel.busy:
            return
        active = self.proxy_catalog.get_active_proxy()
        if not active:
            self.status.set("Chưa có proxy. Thêm và lưu proxy trong tab Quản lý proxy.")
            self.open_proxy()
            return
        item = copy.deepcopy(active)
        self.network_busy = True
        self.network_cancel.clear()
        self.proxy_catalog.suspended = True
        self.settings_panel.set_locked(True)
        self.status.set("Đang yêu cầu đổi IP…" if rotate else "Đang kiểm tra proxy…")
        self._refresh_connection()
        def worker():
            catalog = copy.copy(self.proxy_catalog)
            catalog.save = lambda: None
            try:
                result = catalog.rotate_proxy(item, cancel_event=self.network_cancel) if rotate else catalog.check_proxy(item, auto_rotate_on_fail=False, cancel_event=self.network_cancel)
            except Exception:
                result = {"ok": False}
            self.ui_queue.put(("proxy_result", (item, result)))
        threading.Thread(target=worker, daemon=True).start()

    def apply_settings(self, dialog, workers):
        output = writable_directory(Path(dialog.output.get()))
        settings = self.store.settings.copy()
        settings.update(output_dir=str(output), use_proxy=True,
                        randomize_device=dialog.random.get(), download_via_direct_ip=dialog.direct.get(),
                        concurrency=workers, admin_hash=dialog.admin_hash)
        atomic_json(self.store.root / "settings.json", settings)
        with self.proxy_catalog._lock:
            from dataclasses import asdict
            items = copy.deepcopy(dialog.items)
            current = {item.id: item for item in self.proxy_catalog.items}
            active = next((item.id for item in current.values() if item.is_active), None)
            preserve_active = not dialog.activation_changed and any(item.id == active for item in items)
            for item in items:
                live = current.get(item.id)
                if live and (item.address, item.rotate_url) == (live.address, live.rotate_url):
                    if (live.last_rotate_at or 0) > (item.last_rotate_at or 0):
                        for key in ("current_ip", "latency", "status", "last_rotate_at", "error_message"):
                            setattr(item, key, getattr(live, key))
                if live and (item.address, item.rotate_url, item.rotate_minutes, item.auto_rotate) == (live.address, live.rotate_url, live.rotate_minutes, live.auto_rotate):
                    if live.next_rotate_at is not None and (live.last_rotate_at or 0) >= (item.last_rotate_at or 0):
                        item.next_rotate_at = live.next_rotate_at
                if preserve_active:
                    item.is_active = item.id == active
            atomic_json(self.proxy_catalog.storage_path,
                        {"version": 1, "items": [asdict(item) for item in items]})
            self.proxy_catalog.items = items
            dialog.items = copy.deepcopy(items)
            dialog.activation_changed = False
        dialog.refresh()
        self.store.settings = settings
        self.session_id = dialog.session.get().strip() or None
        self.output_dir.set(str(output))
        self.status.set("Đã lưu cài đặt.")
        self._refresh_traffic()
        self._refresh_connection()

    def copy_support(self):
        content = f"Voice Pro - chất lượng cao nhất\nWindows: {platform.release()}\nMã lỗi: {self.last_error_code or 'none'}\nTrạng thái: {self.busy or 'idle'}"
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status.set("Đã sao chép thông tin hỗ trợ.")

    def open_admin(self, parent=None):
        parent = parent or self.root
        encoded = self.store.settings["admin_hash"]
        if not encoded:
            messagebox.showinfo("Nhật ký quản trị", "Đặt mật khẩu quản trị trong Cài đặt → Hỗ trợ trước khi mở nhật ký.", parent=parent)
            return
        AdminLogDialog(parent, self.log_buffer, encoded)

    def show_licenses(self, parent=None):
        parent = parent or self.root
        bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "THIRD_PARTY_NOTICES.txt"
        paths = [bundled] if bundled.is_file() else sorted((Path(__file__).resolve().parent / "vendor" / "ffmpeg").glob("*.txt"))
        try:
            content = "\n\n".join(path.read_text(encoding="utf-8") for path in paths)
        except OSError:
            content = "Không đọc được thông báo giấy phép. Xem THIRD_PARTY_NOTICES.txt đi kèm bản ứng dụng."
        window = tk.Toplevel(parent)
        window.title("Giấy phép thành phần")
        window.geometry("760x520")
        window.transient(parent)
        previous_grab = parent.grab_current()
        window.grab_set()
        text = tk.Text(window, wrap="word", padx=12, pady=12, font=("Segoe UI", 10))
        scrollbar = ttk.Scrollbar(window, command=text.yview)
        scrollbar.pack(side="right", fill="y")
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(fill="both", expand=True)
        text.insert("1.0", content or "Thông báo giấy phép sẽ có trong bản build đầy đủ.")
        text.configure(state="disabled")
        def close():
            window.destroy()
            if previous_grab and previous_grab.winfo_exists():
                previous_grab.grab_set()
        window.protocol("WM_DELETE_WINDOW", close)

    def log(self, message, level="INFO"):
        message = redact_log(message)
        self.logger.log(getattr(logging, level, logging.INFO), message)
        self.ui_queue.put(("log", dict(time=time.strftime("%H:%M:%S"), level=level, message=message)))

    def close(self):
        if self.busy:
            if not messagebox.askyesno("Đóng ứng dụng", "Dừng tác vụ và thoát?\nCác đoạn đã tạo và bản nháp sẽ được giữ lại.", parent=self.root):
                return
            self.closing = True
            self._stop_generation()
        else:
            self._destroy()

    def _destroy(self):
        self.network_cancel.set()
        self.settings_panel.destroy()
        self._save()
        if self.save_id:
            self.root.after_cancel(self.save_id)
        if hasattr(self, "poll_id"):
            self.root.after_cancel(self.poll_id)
        self.proxy_catalog.stop_scheduler()
        self.player.close()
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
        self.root.destroy()


def launch_gui():
    root = tk.Tk()
    try:
        data = application_data_dir()
    except OSError:
        root.withdraw()
        messagebox.showwarning("Nơi lưu dữ liệu", "Không ghi được vào thư mục dữ liệu. Hãy chọn thư mục khác.", parent=root)
        path = filedialog.askdirectory(parent=root, title="Chọn nơi lưu dữ liệu Voice Pro")
        if not path:
            root.destroy()
            return
        data = Path(path)
        root.deiconify()
    try:
        EverAIGuiApp(root, data)
    except Exception:
        messagebox.showerror("Khởi động", "Không mở được dữ liệu ứng dụng. Kiểm tra quyền ghi thư mục.", parent=root)
        root.destroy()
        raise
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
