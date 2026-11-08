"""Embedded license panel; all widget operations run on Tk's main thread."""
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from .license_manager import LicenseError, MAX_LICENSE_BYTES, expiry_label
except ImportError:
    from license_manager import LicenseError, MAX_LICENSE_BYTES, expiry_label


class LicensePanel(ttk.LabelFrame):
    def __init__(self, parent, app):
        super().__init__(parent, text="Bản quyền · Hạn mức ký tự", padding=10)
        self.app = app
        self.last_refresh = 0
        self.balance_text = tk.StringVar()
        manager = app.license_manager
        self.machine_text = tk.StringVar(value=manager.machine if manager else app.license_error)
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Mã máy:").pack(side="left")
        ttk.Entry(row, textvariable=self.machine_text, state="readonly").pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="Sao chép mã máy", command=self.copy_machine).pack(side="left")
        ttk.Label(self, textvariable=self.balance_text, wraplength=900).pack(anchor="w", pady=8)
        ttk.Label(self, text="Dán key kích hoạt do quản trị viên gửi:").pack(anchor="w")
        key_row = ttk.Frame(self)
        key_row.pack(fill="x", pady=(4, 8))
        self.key_text = tk.StringVar()
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_text)
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.key_entry.bind("<Return>", lambda _: self.activate_key())
        self.activate_button = ttk.Button(key_row, text="Kích hoạt key", command=self.activate_key)
        self.activate_button.pack(side="left")
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        self.import_button = ttk.Button(actions, text="Nhập từ file…", command=self.import_license)
        self.import_button.pack(side="left")
        ttk.Button(actions, text="Làm mới số dư", command=lambda: self.refresh(force=True)).pack(side="left", padx=6)
        ttk.Button(actions, text="Sao lưu ví…", command=self.backup).pack(side="left")
        ttk.Label(self, text="Gửi mã máy cho quản trị viên để nhận gói ký tự. Nhập lại cùng key không cộng thêm.",
                  wraplength=900).pack(anchor="w", pady=(8,0))
        self.refresh(force=True)

    def copy_machine(self):
        if self.app.license_manager:
            self.clipboard_clear()
            self.clipboard_append(self.app.license_manager.machine)
            self.app.status.set("Đã sao chép mã máy. Gửi mã này cho quản trị viên để cấp license.")

    def refresh(self, force=False):
        if not force and time.monotonic()-self.last_refresh < 1:
            return
        self.last_refresh = time.monotonic()
        manager = self.app.license_manager
        try:
            if not manager:
                raise LicenseError(self.app.license_error)
            balance = manager.balance(include_expiry=True)
            deadline = expiry_label(balance["expires_at"])
            expiry_text = ("Đã hết hạn ngày " if balance["expired"] else "Hạn dùng: hết ngày ") + deadline + " (giờ Việt Nam)"
            if balance["expires_at"] is None:
                expiry_text = deadline if balance["total"] else "Chưa kích hoạt license"
            self.balance_text.set(f"Tổng cấp: {balance['total']:,} · Đã dùng: {balance['used']:,} · "
                                  f"Đang giữ: {balance['held']:,} · Còn khả dụng: {balance['available']:,} ký tự\n{expiry_text}" +
                                  (f" · Số dư giữ lại: {balance['remaining']:,} ký tự" if balance["expired"] else ""))
            if hasattr(self.app, "balance_summary"):
                self.app.balance_summary.set("License đã hết hạn" if balance["expired"] else
                                            f"Còn {balance['available']:,} ký tự")
        except (LicenseError, OSError) as error:
            self.balance_text.set(str(error))
            if hasattr(self.app, "balance_summary"):
                self.app.balance_summary.set("Kiểm tra bản quyền")
        state = "disabled" if self.app.busy or manager is None else "normal"
        for widget in (self.import_button, self.activate_button, self.key_entry):
            widget.configure(state=state)

    def activate_key(self):
        if self.app.busy or not self.app.license_manager:
            return
        if not self.key_text.get().strip():
            self.app.status.set("Hãy dán key kích hoạt do quản trị viên gửi.")
            self.key_entry.focus_set()
            return
        if self._apply_license(self.key_text.get()):
            self.key_text.set("")

    def _apply_license(self, data):
        try:
            added = self.app.license_manager.import_license(data)
            self.app.status.set("Đã cộng gói ký tự và cập nhật thời hạn license." if added else "License này đã được nhập trước đó; số dư giữ nguyên.")
            self.refresh(force=True)
            return True
        except (LicenseError, OSError) as error:
            messagebox.showerror("Không nhập được license", str(error), parent=self)
            return False

    def import_license(self):
        if self.app.busy or not self.app.license_manager:
            return
        name = filedialog.askopenfilename(parent=self, filetypes=[("Voice Pro license", "*.everai-license")])
        if not name:
            return
        try:
            with Path(name).open("rb") as stream:
                data = stream.read(MAX_LICENSE_BYTES+1)
            self._apply_license(data)
        except (LicenseError, OSError) as error:
            messagebox.showerror("Không nhập được license", str(error), parent=self)

    def backup(self):
        if self.app.busy or not self.app.license_manager:
            return
        name = filedialog.asksaveasfilename(parent=self, defaultextension=".sqlite3", initialfile="VoicePro-wallet-backup.sqlite3")
        if name:
            try:
                self.app.license_manager.backup_wallet(name)
                self.app.status.set("Đã sao lưu ví ký tự: " + name)
            except (LicenseError, OSError) as error:
                messagebox.showerror("Sao lưu ví", str(error), parent=self)
