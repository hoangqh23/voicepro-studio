"""Staged settings: network tests never touch Tk from their worker thread."""
import copy
import time
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog
try:
    from .proxy_manager import ProxyItem
    from .utils import parse_proxy, password_hash, verify_password
    from .ui_theme import StudioButton, Card
except ImportError:
    from proxy_manager import ProxyItem
    from utils import parse_proxy, password_hash, verify_password
    from ui_theme import StudioButton, Card


class SettingsDialog(ttk.Frame):
    """The same settings editor can live in the workspace or a compatibility dialog."""
    def __init__(self, parent, app, embedded=False, preferences_parent=None):
        self.embedded = embedded
        self.window = None
        if not embedded:
            self.window = tk.Toplevel(parent)
            self.window.title("Cài đặt đầy đủ")
            self.window.geometry("940x720")
            self.window.minsize(740, 560)
            self.window.transient(parent)
            self.window.grab_set()
            parent = self.window
        super().__init__(parent)
        if self.window:
            self.pack(fill="both", expand=True)
            self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.app = app
        self.items = copy.deepcopy(app.proxy_catalog.get_all())
        self.activation_changed = False
        self.selected_id = None
        self.busy = False
        self.responses = queue.Queue()
        self.cancel_event = threading.Event()
        self.locked = False
        body = self
        if not embedded:
            canvas = tk.Canvas(self, highlightthickness=0, bg="#f4f5f7")
            scroll = ttk.Scrollbar(self, command=canvas.yview)
            scroll.pack(side="right", fill="y")
            canvas.configure(yscrollcommand=scroll.set)
            canvas.pack(fill="both", expand=True)
            body = ttk.Frame(canvas)
            handle = canvas.create_window((0, 0), window=body, anchor="nw")
            body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(handle, width=e.width))
        network_card = Card(body, "Danh sách proxy")
        network_card.pack(fill="x", pady=8)
        network = network_card.body
        self.network = network
        self.preferences = ttk.Frame(preferences_parent) if preferences_parent is not None else None
        preferences = self.preferences if self.preferences is not None else body
        self.dialog_parent = preferences.winfo_toplevel()
        if self.preferences is not None:
            self.preferences.pack(fill="x")
        options_card = Card(preferences, "Kết nối và xử lý")
        options_card.pack(fill="x", pady=8)
        options = options_card.body
        general_card = Card(preferences, "Dữ liệu và phục hồi")
        general_card.pack(fill="x", pady=8)
        general = general_card.body
        support_card = Card(preferences, "Hỗ trợ và quản trị")
        support_card.pack(fill="x", pady=8)
        support = support_card.body
        self.output = app.output_dir if embedded else tk.StringVar(value=app.output_dir.get())
        ttk.Label(general, text="Nơi lưu âm thanh mặc định").pack(anchor="w")
        ttk.Entry(general, textvariable=self.output).pack(fill="x", pady=8)
        StudioButton(general, text="Chọn thư mục…", width=145, command=self.browse).pack(anchor="w")
        ttk.Label(general, text=f"Dữ liệu ứng dụng:\n{app.store.root}", wraplength=650).pack(anchor="w", pady=20)
        ttk.Label(general, text="Bản nháp được lưu tự động. Lịch sử chỉ lưu 10 file gần nhất.").pack(anchor="w")
        StudioButton(general, text="Bỏ tác vụ chưa hoàn tất…", width=210, command=self.discard_job).pack(anchor="w", pady=12)

        s = app.store.settings
        self.use_proxy = tk.BooleanVar(value=True)
        self.direct = tk.BooleanVar(value=s["download_via_direct_ip"])
        self.random = tk.BooleanVar(value=s["randomize_device"])
        self.workers = app.workers if embedded else tk.IntVar(value=s["concurrency"])
        self.session = tk.StringVar(value=app.session_id or "")
        ttk.Checkbutton(options, text="Tải MP3 trực tiếp (yêu cầu tạo giọng vẫn dùng proxy nếu bật)", variable=self.direct).pack(anchor="w")
        ttk.Checkbutton(options, text="Thay đổi hồ sơ thiết bị", variable=self.random).pack(anchor="w")
        session_row = ttk.Frame(options)
        session_row.pack(fill="x", pady=4)
        ttk.Label(session_row, text="Session tài khoản (tùy chọn)").pack(side="left")
        ttk.Entry(session_row, textvariable=self.session, show="*", width=24).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(options, text="Session chỉ dùng trong lần mở app này, không ghi vào file.", style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(options)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Số đoạn xử lý đồng thời").pack(side="left")
        ttk.Spinbox(row, from_=1, to=8, textvariable=self.workers, width=5).pack(side="left", padx=8)
        self.summary = tk.StringVar()
        ttk.Label(network, textvariable=self.summary).pack(anchor="w", pady=5)
        table = ttk.Frame(network)
        table.pack(fill="x", pady=6)
        self.tree = ttk.Treeview(table, columns=("name", "kind", "active", "address", "ip", "status", "latency", "schedule"), show="headings", height=5)
        for key, label, width in (("name", "Tên", 160), ("kind", "Loại", 65), ("active", "Đang dùng", 75),
                                  ("address", "Địa chỉ", 170), ("ip", "IP hiện tại", 130),
                                  ("status", "Trạng thái", 130), ("latency", "Độ trễ", 75), ("schedule", "Lịch xoay", 190)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=width, stretch=False)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        vertical = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.name, self.address, self.url, self.minutes = [tk.StringVar() for _ in range(4)]
        self.kind = tk.StringVar(value="static")
        self.auto, self.on429 = tk.BooleanVar(), tk.BooleanVar(value=True)
        form = ttk.Frame(network)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for index, (label, variable) in enumerate((("Tên", self.name), ("Địa chỉ proxy", self.address), ("Link đổi IP", self.url), ("Chu kỳ (phút)", self.minutes))):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=(0, 8), pady=3)
            entry = ttk.Entry(form, textvariable=variable, show="*" if variable is self.address else "")
            entry.grid(row=index, column=1, sticky="ew", pady=3)
            if variable is self.url:
                self.url_entry = entry
            if variable is self.minutes:
                self.minutes_entry = entry
        ttk.Combobox(form, values=("static", "rotating"), textvariable=self.kind, state="readonly", width=12).grid(row=0, column=2, padx=8)
        self.auto_check = ttk.Checkbutton(network, text="Đổi IP định kỳ khi mở app", variable=self.auto)
        self.auto_check.pack(anchor="w")
        self.quota_check = ttk.Checkbutton(network, text="Gọi link đổi IP khi gặp hạn mức", variable=self.on429)
        self.quota_check.pack(anchor="w")
        self.kind.trace_add("write", self.kind_changed)
        self.kind_changed()
        buttons = ttk.Frame(network)
        buttons.pack(fill="x", pady=6)
        for index, (name, command) in enumerate((("Thêm proxy tĩnh", self.new), ("Thêm proxy xoay", self.new_rotating), ("Lưu proxy", self.stage), ("Kích hoạt", self.activate), ("Sửa đã chọn", self.select), ("Xóa", self.delete), ("Kiểm tra", lambda: self.test(False)), ("Đổi IP", lambda: self.test(True)), ("Nạp TXT…", self.import_txt), ("Làm mới trạng thái", self.refresh))):
            buttons.columnconfigure(index % 4, weight=1, uniform="actions")
            StudioButton(buttons, text=name, width=130, command=command).grid(row=index // 4, column=index % 4, sticky="ew", padx=3, pady=3)
        self.status = tk.StringVar(value="Thay đổi chỉ áp dụng sau khi bấm Lưu.")
        ttk.Label(network, textvariable=self.status, wraplength=700).pack(anchor="w")
        ttk.Label(support, text="Voice Pro - chất lượng cao nhất\nNhật ký hỗ trợ đã che thông tin nhạy cảm.").pack(anchor="w", pady=10)
        StudioButton(support, text="Sao chép thông tin hỗ trợ", width=240, command=app.copy_support).pack(anchor="w", pady=6)
        StudioButton(support, text="Giấy phép thành phần…", width=240, command=lambda: app.show_licenses(self.dialog_parent)).pack(anchor="w", pady=6)
        StudioButton(support, text="Nhật ký quản trị…", width=240, command=lambda: app.open_admin(self.dialog_parent)).pack(anchor="w", pady=6)
        StudioButton(support, text="Đặt / thay mật khẩu quản trị…", width=240, command=self.change_password).pack(anchor="w", pady=6)
        self.admin_hash = s["admin_hash"]
        for container in (body, preferences) if self.preferences is not None else (body,):
            footer = ttk.Frame(container, padding=(0, 8))
            footer.pack(fill="x")
            StudioButton(footer, text="Lưu cài đặt", style="Primary.TButton", width=120, command=self.save).pack(side="right")
            StudioButton(footer, text="Hủy thay đổi" if embedded else "Hủy", width=120, command=self.cancel).pack(side="right", padx=8)
        self.refresh()
        self.poll_id = self.after(100, self.poll)

    def browse(self):
        path = filedialog.askdirectory(parent=self.dialog_parent)
        if path:
            self.output.set(path)

    def kind_changed(self, *_):
        enabled = self.kind.get() == "rotating"
        for widget in (self.url_entry, self.minutes_entry, self.auto_check, self.quota_check):
            widget.configure(state="normal" if enabled else "disabled")

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.summary.set(f"Tổng: {len(self.items)} · Proxy tĩnh: {sum(p.kind == 'static' for p in self.items)} · Proxy xoay: {sum(p.kind == 'rotating' for p in self.items)}")
        for item in self.items:
            parsed = parse_proxy(item.address)
            address = f"{parsed.host}:{parsed.port}" if parsed else "Không hợp lệ"
            schedule = f"{item.rotate_minutes} phút · {'Bật' if item.auto_rotate else 'Tắt'} · 429: {'Bật' if item.rotate_on_429 else 'Tắt'}" if item.rotate_url else "Không xoay"
            self.tree.insert("", "end", iid=item.id, values=(item.name, "Xoay" if item.kind == "rotating" else "Tĩnh", "Có" if item.is_active else "", address, item.current_ip or "—", item.status, f"{item.latency} ms" if item.latency is not None else "—", schedule))
        if self.selected_id and self.tree.exists(self.selected_id):
            self.tree.selection_set(self.selected_id)

    def select(self, _=None):
        if self.busy or self.locked:
            return
        selected = self.tree.selection()
        item = next((p for p in self.items if selected and p.id == selected[0]), None)
        if not item:
            return
        self.selected_id = item.id
        for variable, value in ((self.name, item.name), (self.address, item.address), (self.url, item.rotate_url or ""), (self.minutes, str(item.rotate_minutes)), (self.kind, item.kind), (self.auto, item.auto_rotate), (self.on429, item.rotate_on_429)):
            variable.set(value)

    def selection_changed(self, _=None):
        selection = self.tree.selection()
        if selection and selection[0] != self.selected_id:
            self.select()

    def new(self):
        if self.busy or self.locked:
            return
        self.selected_id = None
        self.name.set("")
        self.address.set("")
        self.url.set("")
        self.minutes.set("5")
        self.kind.set("static")
        self.auto.set(False)
        self.on429.set(True)

    def new_rotating(self):
        if self.busy or self.locked:
            return
        self.new()
        self.kind.set("rotating")
        self.auto.set(True)

    def stage(self):
        if self.busy or self.locked:
            return False
        parsed = parse_proxy(self.address.get())
        if not parsed or not parsed.host or not 1 <= parsed.port <= 65535:
            self.status.set("Địa chỉ chưa hợp lệ. Dùng IP:Port hoặc IP:Port:User:Pass.")
            return False
        try:
            minutes = int(self.minutes.get() or "5")
            if minutes < 1:
                raise ValueError()
        except ValueError:
            self.status.set("Chu kỳ phải là số phút lớn hơn 0.")
            return False
        rotating = self.kind.get() == "rotating"
        if rotating and not self.url.get().startswith(("https://", "http://")):
            self.status.set("Link đổi IP phải bắt đầu bằng http:// hoặc https://.")
            return False
        item = next((p for p in self.items if p.id == self.selected_id), None)
        if item is None:
            item = ProxyItem(is_active=False)
            self.items.append(item)
        old_schedule = (item.address, item.rotate_url, item.rotate_minutes, item.auto_rotate)
        item.name, item.address = self.name.get().strip() or "Proxy", self.address.get().strip()
        item.kind, item.rotate_minutes = self.kind.get(), minutes
        item.rotate_url = self.url.get().strip() if rotating else ""
        item.auto_rotate, item.rotate_on_429 = rotating and self.auto.get(), rotating and self.on429.get()
        if not item.auto_rotate:
            item.next_rotate_at = None
        elif old_schedule != (item.address, item.rotate_url, item.rotate_minutes, item.auto_rotate) or item.next_rotate_at is None:
            item.next_rotate_at = time.time() + minutes * 60
        self.selected_id = item.id
        self.refresh()
        self.status.set("Đã cập nhật bản nháp danh sách. Bấm Lưu để áp dụng.")
        return True

    def activate(self):
        if self.busy or self.locked or not self.selected_id:
            return
        self.activation_changed = True
        for item in self.items:
            item.is_active = item.id == self.selected_id
        self.refresh()

    def delete(self):
        if self.busy or self.locked or not self.selected_id:
            return
        self.items = [p for p in self.items if p.id != self.selected_id]
        self.new()
        self.refresh()

    def import_txt(self):
        if self.busy or self.locked:
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[("Danh sách proxy", "*.txt")])
        if path:
            try:
                for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
                    if parse_proxy(line):
                        self.items.append(ProxyItem(name=f"Proxy {len(self.items) + 1}", address=line.strip(), is_active=False))
                self.refresh()
            except (OSError, UnicodeError):
                self.status.set("Không đọc được file. Hãy chọn file TXT UTF-8.")

    def test(self, rotate):
        if self.busy or self.locked or not self.stage():
            return
        item = copy.deepcopy(next(p for p in self.items if p.id == self.selected_id))
        self.busy = True
        self.cancel_event.clear()
        self.status.set("Đang kiểm tra kết nối…" if not rotate else "Đang yêu cầu đổi IP…")
        def worker():
            try:
                # Test staged values without persisting the working app catalog.
                catalog = copy.copy(self.app.proxy_catalog)
                catalog.save = lambda: None
                result = catalog.rotate_proxy(item, cancel_event=self.cancel_event) if rotate else catalog.check_proxy(item, auto_rotate_on_fail=False, cancel_event=self.cancel_event)
                self.responses.put((item, result))
            except Exception:
                self.responses.put((item, {"ok": False}))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            item, result = self.responses.get_nowait()
            self.busy = False
            for current in self.items:
                if current.id == item.id:
                    for key in ("current_ip", "latency", "status", "last_rotate_at", "next_rotate_at", "error_message"):
                        setattr(current, key, getattr(item, key))
            self.refresh()
            self.status.set(f"Kết nối tốt · IP: {item.current_ip or result.get('ip', '—')} · {item.latency if item.latency is not None else '—'} ms" if result.get("ok") else "Chưa kết nối được. Kiểm tra địa chỉ và nhà cung cấp.")
        except queue.Empty:
            pass
        self.poll_id = self.after(100, self.poll)

    def change_password(self):
        if self.admin_hash:
            old = simpledialog.askstring("Mật khẩu quản trị", "Mật khẩu hiện tại:", show="*", parent=self.dialog_parent)
            if not verify_password(old or "", self.admin_hash):
                messagebox.showerror("Mật khẩu", "Mật khẩu hiện tại không đúng.", parent=self.dialog_parent)
                return
        password = simpledialog.askstring("Mật khẩu quản trị", "Mật khẩu mới (ít nhất 8 ký tự):", show="*", parent=self.dialog_parent)
        if not password:
            return
        if len(password) < 8:
            messagebox.showerror("Mật khẩu", "Cần ít nhất 8 ký tự.", parent=self.dialog_parent)
            return
        repeated = simpledialog.askstring("Mật khẩu quản trị", "Nhập lại mật khẩu mới:", show="*", parent=self.dialog_parent)
        if password != repeated:
            messagebox.showerror("Mật khẩu", "Hai lần nhập chưa khớp.", parent=self.dialog_parent)
            return
        self.admin_hash = password_hash(password)
        messagebox.showinfo("Mật khẩu", "Bấm Lưu trong Cài đặt để áp dụng mật khẩu mới.", parent=self.dialog_parent)

    def save(self):
        if self.busy or self.locked or self.app.busy:
            self.status.set("Chờ kiểm tra kết nối xong trước khi lưu.")
            return False
        try:
            if self.address.get().strip() and not self.stage():
                return
            workers = int(self.workers.get())
            if not 1 <= workers <= 8:
                raise ValueError()
            self.app.apply_settings(self, workers)
            if not self.embedded:
                self.destroy()
            else:
                self.status.set("Đã lưu cài đặt và danh sách proxy.")
            return True
        except (ValueError, OSError):
            self.status.set("Không lưu được. Kiểm tra số luồng (1–8) và quyền ghi thư mục.")
            return False

    def discard_job(self):
        if self.locked or self.app.busy:
            return
        jobs = self.app.store.unfinished_jobs()
        if not jobs:
            messagebox.showinfo("Tác vụ", "Không có tác vụ dở.", parent=self)
            return
        if messagebox.askyesno("Bỏ tác vụ dở", "Xóa dữ liệu phục hồi của các tác vụ dở? File MP3 đã xuất được giữ nguyên.", parent=self):
            base = (self.app.store.root / "jobs").resolve()
            for path, _ in jobs:
                if path.resolve().parent == base:
                    shutil.rmtree(path)
            self.app.pending_job = None
            self.app.refresh_recovery()

    def cancel(self):
        if self.busy or self.locked:
            self.status.set("Chờ kiểm tra kết nối xong trước khi đóng.")
        elif self.embedded:
            self.items = copy.deepcopy(self.app.proxy_catalog.get_all())
            self.activation_changed = False
            s = self.app.store.settings
            for var, value in ((self.use_proxy, s["use_proxy"]), (self.direct, s["download_via_direct_ip"]),
                               (self.random, s["randomize_device"]), (self.workers, s["concurrency"]),
                               (self.output, s["output_dir"]), (self.session, self.app.session_id or "")):
                var.set(value)
            self.admin_hash = s["admin_hash"]
            self.new()
            self.refresh()
            self.status.set("Đã hủy các thay đổi chưa lưu.")
        else:
            self.destroy()

    def set_locked(self, locked):
        if locked == self.locked:
            return
        self.locked = locked
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                if not isinstance(child, StudioButton):
                    yield from descendants(child)
        if locked:
            self.saved_states = []
            for container in (self, self.preferences) if self.preferences is not None else (self,):
                for widget in descendants(container):
                    if isinstance(widget, StudioButton) or "state" in widget.keys():
                        self.saved_states.append((widget, widget.cget("state")))
                        widget.configure(state="disabled")
        else:
            for widget, state in self.saved_states:
                widget.configure(state=state)
            self.kind_changed()

    def destroy(self):
        self.cancel_event.set()
        if hasattr(self, "poll_id"):
            self.after_cancel(self.poll_id)
        super().destroy()
        if self.window and self.window.winfo_exists():
            self.window.destroy()
