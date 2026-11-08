"""Authenticated, bounded log viewer. Entries have already been redacted at source."""
import tkinter as tk
from tkinter import ttk, simpledialog, filedialog
try:
    from .utils import verify_password, redact_log
except ImportError:
    from utils import verify_password, redact_log


def format_log_entry(entry):
    return f"[{entry.get('time', '')}] [{entry.get('level', 'INFO')}] {redact_log(entry.get('message', ''))}"


def AdminLogDialog(parent, log_buffer, admin_password):
    password = simpledialog.askstring("Nhật ký quản trị", "Mật khẩu quản trị:", show="*", parent=parent)
    if password is None:
        return None
    if not verify_password(password, admin_password):
        from tkinter import messagebox
        messagebox.showerror("Nhật ký quản trị", "Mật khẩu không đúng.", parent=parent)
        return None
    window = tk.Toplevel(parent)
    window.title("Nhật ký quản trị")
    window.geometry("820x500")
    window.transient(parent)
    previous_grab = parent.grab_current()
    window.grab_set()
    def close():
        window.destroy()
        if previous_grab and previous_grab.winfo_exists():
            previous_grab.grab_set()
    window.protocol("WM_DELETE_WINDOW", close)
    level = tk.StringVar(value="Tất cả")
    toolbar = ttk.Frame(window, padding=10)
    toolbar.pack(fill="x")
    ttk.Combobox(toolbar, textvariable=level, state="readonly", width=12,
                 values=("Tất cả", "INFO", "WARNING", "ERROR", "DEBUG")).pack(side="left")
    text = tk.Text(window, wrap="word", font=("Consolas", 10))
    scroll = ttk.Scrollbar(window, command=text.yview)
    scroll.pack(side="right", fill="y")
    text.configure(yscrollcommand=scroll.set)
    text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def content():
        return "\n".join(format_log_entry(e) for e in list(log_buffer)
                         if level.get() == "Tất cả" or e["level"] == level.get())

    def refresh():
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", content())
        text.configure(state="disabled")
        text.see("end")

    def copy():
        window.clipboard_clear()
        window.clipboard_append(content())

    def save():
        path = filedialog.asksaveasfilename(parent=window, defaultextension=".log")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write(content())
            except OSError:
                from tkinter import messagebox
                messagebox.showerror("Nhật ký", "Không lưu được. Hãy chọn vị trí khác.", parent=window)

    ttk.Button(toolbar, text="Làm mới", command=refresh).pack(side="left", padx=5)
    ttk.Button(toolbar, text="Sao chép", command=copy).pack(side="right")
    ttk.Button(toolbar, text="Lưu file…", command=save).pack(side="right", padx=5)
    ttk.Button(toolbar, text="Xóa bộ đệm", command=lambda: (log_buffer.clear(), refresh())).pack(side="right")
    level.trace_add("write", lambda *_: refresh())
    refresh()
    return window
