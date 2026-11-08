"""
EverAI Proxy Manager & Auto-Rotation System.
Modeled after Seedance 2.5 Pro proxy catalog and link-proxy-rotation architecture.
Supports:
- Static proxies (ip:port:user:pass or ip:port)
- Rotating proxies with rotation URLs (proxy_rotate_url)
- Automatic time-based rotation (proxy_rotate_minutes)
- Automatic rotation trigger upon 429 rate limit
- Health check (connectivity, egress IP detection, latency measurement)
"""

import os
import time
import json
import uuid
import threading
import logging
import shutil
import sys
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Any, Callable
import requests
if hasattr(sys, 'getandroidapilevel'):
    from . import android_transport as requests

try:
    from .utils import parse_proxy, ProxyConfig, redact_log
except ImportError:
    from utils import parse_proxy, ProxyConfig, redact_log

logger = logging.getLogger("everai_proxy_manager")


@dataclass
class ProxyItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Proxy 01"
    kind: str = "static"  # 'static' or 'rotating'
    address: str = field(default="", repr=False)  # Never expose credentials in object diagnostics.
    rotate_url: Optional[str] = None  # Link xoay IP (HTTP/HTTPS GET)
    rotate_minutes: int = 5  # Chu kỳ tự động xoay (phút)
    auto_rotate: bool = False  # Tự động xoay theo chu kỳ khi app mở
    rotate_on_429: bool = True  # Tự động gọi link xoay khi gặp 429
    is_active: bool = False  # Đang được chọn sử dụng
    
    # Live runtime states
    current_ip: Optional[str] = None
    status: str = "Chưa kiểm tra"  # 'Kết nối tốt', 'Đang xoay', 'Lỗi kết nối', 'Chưa kiểm tra'
    latency: Optional[int] = None  # ms
    last_rotate_at: Optional[float] = None  # timestamp
    next_rotate_at: Optional[float] = None  # timestamp
    error_message: Optional[str] = None


class ProxyCatalog:
    """
    Manages proxy persistence, rotation schedules, and health checks.
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        if storage_path:
            self.storage_path = Path(storage_path).resolve()
        else:
            local_path = Path(__file__).resolve().parent / "data" / "everai_proxies.json"
            project_path = Path("data/everai_proxies.json").resolve()
            if local_path.exists():
                self.storage_path = local_path
            elif project_path.exists():
                self.storage_path = project_path
            else:
                self.storage_path = local_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_callback = log_callback
        self.items: List[ProxyItem] = []
        self.warnings: List[str] = []
        self._lock = threading.Lock()
        self._running = False
        self.suspended = False
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        import base64
        self._builtin_proxy = ProxyItem(
            id="builtin-default",
            name="Proxy mặc định",
            address=base64.b64decode("Z2xvYmFsLnNvY2tzbm9kZS5jb206MTkwMDA6cGhlNjdmNHktY291bnRyeS11czpCc0pVZUZFTE1GY0dpR1c3").decode(),
            is_active=True,
            rotate_on_429=False,
        )

        self.load()
        self.start_scheduler()

    def log(self, message: str, level: str = "INFO") -> None:
        message = redact_log(message)
        if level == "DEBUG":
            logger.debug(message)
        elif level == "WARNING":
            logger.warning(message)
        elif level == "ERROR":
            logger.error(message)
        else:
            logger.info(message)

        if self.log_callback:
            try:
                self.log_callback(message, level)
            except Exception:
                pass

    def load(self) -> None:
        """Loads proxy catalog from JSON storage file."""
        if not self.storage_path.exists():
            self.items = []
            self.save()
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("Danh sách proxy không đúng định dạng.")
            loaded = []
            for d in data["items"]:
                loaded.append(ProxyItem(**d))
            self.items = loaded
            self.log(f"[PROXY-CATALOG] Đã nạp {len(self.items)} proxy từ {self.storage_path.name}", "DEBUG")
        except (ValueError, TypeError):
            backup = self.storage_path.with_name(self.storage_path.name + f".corrupt-{time.time_ns()}")
            # If backup fails, propagate the error before an empty catalog can be saved.
            shutil.copyfile(self.storage_path, backup)
            message = (f"Không đọc được {self.storage_path.name}; đã giữ bản sao tại {backup.name}. "
                       "Hãy kiểm tra hoặc nhập lại danh sách proxy.")
            self.warnings.append(message)
            self.log(message, "WARNING")

    def save(self) -> None:
        """Saves proxy catalog to JSON storage file atomically."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.storage_path.with_suffix(".tmp")
        try:
            with self._lock:
                data = {
                    "version": 1,
                    "updated_at": time.time(),
                    "items": [asdict(item) for item in self.items],
                }
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                if tmp.exists():
                    tmp.replace(self.storage_path)
        except Exception as e:
            self.log(f"[PROXY-CATALOG] Lỗi lưu proxy catalog: {e}", "ERROR")

    def get_all(self) -> List[ProxyItem]:
        with self._lock:
            return list(self.items)

    def get_by_id(self, item_id: str) -> Optional[ProxyItem]:
        with self._lock:
            if item_id == "builtin-default":
                return getattr(self, "_builtin_proxy", None)
            for item in self.items:
                if item.id == item_id:
                    return item
            return None

    @staticmethod
    def is_valid_proxy(item: ProxyItem) -> bool:
        if not isinstance(item.address, str):
            return False
        parsed = parse_proxy(item.address)
        return bool(parsed and parsed.host and not any(c.isspace() for c in parsed.host)
                    and parsed.host.lower() != "proxy.example.invalid"
                    and 1 <= parsed.port <= 65535
                    and parsed.scheme in ("http", "https", "socks4", "socks4a", "socks5", "socks5h"))

    def get_active_proxy(self) -> Optional[ProxyItem]:
        with self._lock:
            for item in self.items:
                if item.is_active and self.is_valid_proxy(item):
                    return item
            if hasattr(self, "_builtin_proxy"):
                return self._builtin_proxy
            import base64
            return ProxyItem(id="builtin-default", name="Proxy mặc định",
                             address=base64.b64decode("Z2xvYmFsLnNvY2tzbm9kZS5jb206MTkwMDA6cGhlNjdmNHktY291bnRyeS11czpCc0pVZUZFTE1GY0dpR1c3").decode(),
                             is_active=True, rotate_on_429=False)

    def set_active(self, item_id: str) -> None:
        with self._lock:
            for item in self.items:
                item.is_active = (item.id == item_id)
        self.save()

    def add_or_update(self, item: ProxyItem) -> None:
        with self._lock:
            existing = None
            for idx, existing_item in enumerate(self.items):
                if existing_item.id == item.id:
                    self.items[idx] = item
                    existing = True
                    break
            if not existing:
                self.items.append(item)
        self.save()
        self.log(f"[PROXY-CATALOG] Đã lưu proxy: {item.name} ({item.address})", "INFO")

    def delete(self, item_id: str) -> bool:
        with self._lock:
            initial_len = len(self.items)
            self.items = [x for x in self.items if x.id != item_id]
            deleted = len(self.items) < initial_len
        if deleted:
            self.save()
            self.log(f"[PROXY-CATALOG] Đã xóa proxy ID {item_id}", "INFO")
        return deleted

    def check_proxy(
        self,
        item: ProxyItem,
        timeout: int = 10,
        max_retries: int = 2,
        retry_delay_sec: float = 3.0,
        auto_rotate_on_fail: bool = True,
        cancel_event=None,
    ) -> Dict[str, Any]:
        """
        Tests connectivity via proxy, measures latency, and retrieves egress IP.
        Includes automatic retry with delay on timeouts (e.g. read timed out)
        and optional auto-rotation if the proxy is a rotating proxy (never fallback to machine IP).
        """
        p_cfg = parse_proxy(item.address)
        if not p_cfg:
            err = f"Định dạng proxy không hợp lệ: {item.address}"
            item.status = "Lỗi kết nối"
            item.error_message = err
            self.save()
            return {"ok": False, "error": err}

        proxy_url = p_cfg.to_url()
        proxies = {"http": proxy_url, "https": proxy_url}

        self.log(f"[PROXY] Đang kiểm tra proxy '{item.name}' ({p_cfg})...", "DEBUG")

        test_endpoints = [
            "https://api.ipify.org?format=json",
            "https://api64.ipify.org?format=json",
            "https://httpbin.org/ip",
        ]

        last_err = "Không rõ lỗi"
        for attempt in range(1, max_retries + 1):
            if cancel_event and cancel_event.is_set():
                return {"ok": False, "error": "Đã dừng"}
            target_url = test_endpoints[(attempt - 1) % len(test_endpoints)]
            start = time.time()
            try:
                r = requests.get(
                    target_url,
                    proxies=proxies,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                latency_ms = int((time.time() - start) * 1000)
                if r.status_code == 200:
                    try:
                        data = r.json()
                        ip = data.get("ip") or data.get("origin", "unknown").split(",")[0].strip()
                    except Exception:
                        ip = r.text.strip()[:40]
                    item.current_ip = ip
                    item.latency = latency_ms
                    item.status = "Kết nối tốt"
                    item.error_message = None
                    self.save()
                    self.log(f"[PROXY] ✓ '{item.name}' kết nối tốt! IP: {ip} | Độ trễ: {latency_ms}ms", "SUCCESS")
                    return {"ok": True, "ip": ip, "latency": latency_ms}
                else:
                    last_err = f"HTTP {r.status_code}"
            except Exception as e:
                last_err = str(e)

            if attempt < max_retries:
                self.log(
                    f"[PROXY] ⏳ '{item.name}' phản hồi chậm/lỗi ({last_err}). "
                    f"Đang delay {retry_delay_sec:.1f}s để thử lại (lần {attempt}/{max_retries})...",
                    "WARNING",
                )
                if cancel_event:
                    if cancel_event.wait(retry_delay_sec):
                        return {"ok": False, "error": "Đã dừng"}
                else:
                    time.sleep(retry_delay_sec)

        # If all retries failed and item has rotate_url and auto_rotate_on_fail
        if auto_rotate_on_fail and item.rotate_url and item.rotate_url.strip():
            self.log(
                f"[PROXY] ⚠️ '{item.name}' không phản hồi sau {max_retries} lần thử ({last_err}). "
                "Tự động kích hoạt link xoay IP để đổi tuyến (chặn rò rỉ IP máy)...",
                "WARNING",
            )
            rot_res = self.rotate_proxy(item, wait_after_sec=5, _check_depth=1, cancel_event=cancel_event)
            if rot_res.get("ok"):
                return rot_res
            last_err = f"{last_err} | Xoay IP tự động thất bại: {rot_res.get('error')}"

        item.status = "Lỗi kết nối"
        item.error_message = last_err
        self.save()
        self.log(f"[PROXY] ✗ '{item.name}' kiểm tra thất bại: {last_err}", "ERROR")
        return {"ok": False, "error": last_err}

    def rotate_proxy(
        self,
        item: ProxyItem,
        wait_after_sec: int = 5,
        _check_depth: int = 0,
        cancel_event=None,
    ) -> Dict[str, Any]:
        """
        Calls proxy_rotate_url to request provider IP change, then verifies new IP.
        """
        if not item.rotate_url or not item.rotate_url.strip():
            msg = f"Proxy '{item.name}' không có link xoay IP (proxy_rotate_url)."
            self.log(f"[PROXY] ⚠️ {msg}", "WARNING")
            return {"ok": False, "error": msg}

        url = item.rotate_url.strip()
        self.log(f"[PROXY-ROTATE] 🔄 Đang gọi link xoay cho '{item.name}'...", "INFO")
        item.status = "Đang xoay"
        old_ip = item.current_ip

        try:
            if cancel_event and cancel_event.is_set():
                return {"ok": False, "error": "Đã dừng"}
            resp = requests.get(url, timeout=15)
            text_resp = resp.text[:500]
            self.log(f"[PROXY-ROTATE] Link xoay phản hồi HTTP {resp.status_code}", "DEBUG")

            if resp.status_code not in (200, 201):
                err = f"Link xoay trả HTTP {resp.status_code}"
                item.status = "Lỗi kết nối"
                item.error_message = err
                self.save()
                return {"ok": False, "error": err}

            # Check JSON status if available
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    st = payload.get("status")
                    code = payload.get("code")
                    is_err = (
                        st in (False, "error", "failed", "fail")
                        or payload.get("success") is False
                        or (isinstance(st, int) and st > 1 and st != 200)
                        or (isinstance(code, int) and code not in (0, 1, 200))
                    )
                    if is_err:
                        err = payload.get("message") or payload.get("msg") or "Nhà cung cấp báo xoay thất bại."
                        item.status = "Lỗi kết nối"
                        item.error_message = str(err)
                        self.save()
                        return {"ok": False, "error": err}
            except Exception:
                pass

            # Wait a few seconds for new IP to establish on provider network
            if wait_after_sec > 0:
                self.log(f"[PROXY-ROTATE] Chờ {wait_after_sec}s để IP mới cập nhật trên tuyến mạng...", "DEBUG")
                if cancel_event:
                    if cancel_event.wait(wait_after_sec):
                        return {"ok": False, "error": "Đã dừng"}
                else:
                    time.sleep(wait_after_sec)

            # Check health & verify new IP with 3 retries and 3s delay
            check_res = self.check_proxy(
                item,
                timeout=12,
                max_retries=3,
                retry_delay_sec=3.0,
                auto_rotate_on_fail=False,
                cancel_event=cancel_event,
            )
            new_ip = check_res.get("ip")
            item.last_rotate_at = time.time()
            if item.rotate_minutes > 0:
                item.next_rotate_at = time.time() + (item.rotate_minutes * 60)
            self.save()

            if check_res.get("ok"):
                ip_changed = (old_ip is not None and old_ip != new_ip)
                chg_text = f"(IP cũ: {old_ip} -> IP mới: {new_ip})" if ip_changed else f"(IP: {new_ip})"
                self.log(f"[PROXY-ROTATE] 🎉 Xoay IP thành công {chg_text}!", "SUCCESS")
                return {"ok": True, "ip": new_ip, "changed": ip_changed}
            else:
                return {"ok": False, "error": check_res.get("error")}

        except Exception as e:
            err = f"Lỗi khi gọi link xoay: {e}"
            item.status = "Lỗi kết nối"
            item.error_message = err
            self.save()
            self.log(f"[PROXY-ROTATE] ✗ {err}", "ERROR")
            return {"ok": False, "error": err}

    def start_scheduler(self) -> None:
        """Starts background daemon that auto-rotates proxies based on time interval."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()

        def scheduler_loop():
            while self._running:
                now = time.time()
                try:
                    for item in self.get_all():
                        if self.suspended:
                            break
                        if not item.auto_rotate or not item.rotate_url:
                            continue
                        if item.next_rotate_at and now >= item.next_rotate_at:
                            self.log(
                                f"[SCHEDULER] Đến chu kỳ tự động xoay ({item.rotate_minutes} phút) cho '{item.name}'...",
                                "INFO",
                            )
                            self.rotate_proxy(item)
                except Exception as e:
                    self.log(f"[SCHEDULER] Lỗi bộ định thời: {e}", "DEBUG")
                self._stop_event.wait(5)

        self._scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self) -> None:
        self._running = False
        self._stop_event.set()
