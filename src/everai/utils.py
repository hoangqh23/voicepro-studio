"""
EverAI Internal Utilities - Standalone Helpers for Text, Proxy, and Audio.
Ensures src/everai is 100% independent and self-contained with no external project dependencies.
"""

import os
import re
import shutil
import tempfile
import subprocess
import sys
import json
import unicodedata
import hashlib
import secrets
import time
import sqlite3
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


# =============================================================================
# 1. PROXY PARSER & CONFIG
# =============================================================================

@dataclass
class ProxyConfig:
    """Structured representation of a network proxy with optional authentication."""
    host: str
    port: int
    scheme: str = "http"
    username: Optional[str] = None
    password: Optional[str] = None
    raw: str = ""

    @property
    def has_auth(self) -> bool:
        return bool(self.username and self.password)

    def to_url(self) -> str:
        if self.has_auth:
            return f"{self.scheme}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.scheme}://{self.host}:{self.port}"

    def __str__(self) -> str:
        if self.has_auth:
            return f"{self.host}:{self.port}:{self.username}:***"
        return f"{self.host}:{self.port}"


def parse_proxy(proxy_str: Optional[str]) -> Optional[ProxyConfig]:
    """
    Parses proxy strings into ProxyConfig supporting all standard formats:
      1. IP:PORT:USER:PASS       -> 160.250.47.204:28018:HyjvAb:dPaJvx
      2. SCHEME://IP:PORT:USER:PASS
      3. USER:PASS@IP:PORT
      4. SCHEME://USER:PASS@IP:PORT
      5. IP:PORT
      6. SCHEME://IP:PORT
    """
    if not proxy_str:
        return None

    raw = proxy_str.strip().strip("'").strip('"')
    if not raw:
        return None

    scheme = "http"
    work = raw

    if "://" in work:
        scheme_part, _, work = work.partition("://")
        scheme = scheme_part.lower()

    if "@" in work:
        auth_part, _, host_port_part = work.partition("@")
        user, _, pwd = auth_part.partition(":")
        host, _, port_s = host_port_part.partition(":")
        try:
            port = int(port_s)
            return ProxyConfig(
                host=host,
                port=port,
                scheme=scheme,
                username=user or None,
                password=pwd or None,
                raw=raw,
            )
        except ValueError:
            return None

    parts = work.split(":")
    if len(parts) == 4:
        host, port_s, user, pwd = parts
        try:
            port = int(port_s)
            return ProxyConfig(
                host=host,
                port=port,
                scheme=scheme,
                username=user,
                password=pwd,
                raw=raw,
            )
        except ValueError:
            return None

    if len(parts) == 2:
        host, port_s = parts
        try:
            port = int(port_s)
            return ProxyConfig(
                host=host,
                port=port,
                scheme=scheme,
                username=None,
                password=None,
                raw=raw,
            )
        except ValueError:
            return None

    return None


# =============================================================================
# 2. TEXT CHUNKING & SRT PARSER
# =============================================================================

def normalize_tts_text(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def split_text_into_chunks(text: str, max_length: int = 450) -> List[str]:
    """
    Split text into logical chunks under max_length by sentence punctuation
    (periods, exclamation marks, question marks, commas, newlines).
    EverAI enforces a hard limit of 500 chars, so 450 is safe and natural.
    """
    if max_length < 1:
        raise ValueError("Độ dài đoạn phải lớn hơn 0.")
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    sentences = re.split(r"(?<=[.!?。！？\n])\s*", text)
    chunks: List[str] = []
    current_chunk = ""

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current_chunk) + len(s) + 1 <= max_length:
            current_chunk = f"{current_chunk} {s}".strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(s) > max_length:
                sub_parts = re.split(r"(?<=[,;，；])\s*", s)
                sub_chunk = ""
                for part in sub_parts:
                    part = part.strip()
                    if len(sub_chunk) + len(part) + 1 <= max_length:
                        sub_chunk = f"{sub_chunk} {part}".strip()
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk)
                        sub_chunk = part
                if sub_chunk:
                    chunks.append(sub_chunk)
                current_chunk = ""
            else:
                current_chunk = s

    if current_chunk:
        chunks.append(current_chunk)

    # A long punctuation-free phrase still needs a hard bound.
    bounded = []
    for chunk in chunks:
        while len(chunk) > max_length:
            boundary = chunk.rfind(" ", 0, max_length + 1)
            boundary = boundary if boundary > 0 else max_length
            bounded.append(chunk[:boundary].strip())
            chunk = chunk[boundary:].strip()
        if chunk:
            bounded.append(chunk)
    return bounded


def parse_srt_file(srt_path: str) -> List[Dict[str, Any]]:
    """
    Parse an .srt file into a structured list of subtitle segments:
    [{ 'index': 1, 'start_sec': 0.5, 'end_sec': 2.3, 'text': '...' }]
    """
    with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    blocks = re.split(r"\n\s*\n", content.strip())
    subtitles = []

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue

        time_line = None
        text_lines = []
        for l in lines:
            if "-->" in l:
                time_line = l
            elif time_line is not None:
                text_lines.append(l)

        if not time_line or not text_lines:
            continue

        m = re.match(
            r"(\d+):(\d+):(\d+)[,\.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,\.](\d+)",
            time_line,
        )
        if m:
            h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
            start_sec = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
            end_sec = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
            text = " ".join(text_lines)
            subtitles.append(
                {
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "duration": end_sec - start_sec,
                    "text": text,
                }
            )

    return subtitles


# =============================================================================
# 3. AUDIO STITCHING VIA FFMPEG OR BINARY CONCAT
# =============================================================================

def find_ffmpeg_binary() -> Optional[str]:
    """Finds ffmpeg executable from local folder, parent folders, or system PATH."""
    if hasattr(sys, 'getandroidapilevel'):
        return 'android-ffmpegkit'
    search_dirs = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "bin",
        Path(__file__).resolve().parent / "vendor" / "ffmpeg" / "bin",
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
        Path(__file__).resolve().parent.parent.parent,
        Path.cwd(),
    ]
    for d in search_dirs:
        for name in (("ffmpeg",) if sys.platform == "darwin" else ("ffmpeg.exe",)):
            candidate = d / name
            if candidate.is_file():
                return str(candidate)

    found = shutil.which("ffmpeg")
    return found or None


def ensure_ffmpeg() -> Optional[str]:
    return find_ffmpeg_binary()


def voice_demo_path(voice_id: str) -> Optional[Path]:
    """Return a verified, bundled demo. Preview clicks never require a network call."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
        return None
    directory = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "voice_samples"
    try:
        record = read_json(directory / "manifest.json")["voices"][voice_id]
        if record["file"] != voice_id + ".mp3":
            return None
        path = directory / record["file"]
        if path.stat().st_size != record["bytes"] or path.stat().st_size > 16 * 1024 * 1024:
            return None
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            return None
        return path
    except (OSError, ValueError, KeyError, TypeError):
        return None


class AudioAssemblyError(RuntimeError):
    """FFmpeg is missing, unusable, or could not produce the final audio."""


def run_audio_command(command, *, cancel_event=None, timeout=180, text=False):
    """Run FFmpeg with a deadline and reap it before returning on cancel/error."""
    try:
        from .client import EverAICancelled
    except ImportError:
        from client import EverAICancelled
    if cancel_event is not None and cancel_event.is_set():
        raise EverAICancelled("Đã dừng tác vụ.")
    started = time.monotonic()
    try:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=text,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise EverAICancelled("Đã dừng tác vụ.")
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise AudioAssemblyError("FFmpeg quá thời gian xử lý. Các đoạn đã tạo được giữ lại.")
                    try:
                        stdout, stderr = process.communicate(timeout=min(.1, remaining))
                        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                    except subprocess.TimeoutExpired:
                        pass
            except BaseException:
                if process.poll() is None:
                    process.kill()
                process.communicate()
                raise
    except OSError as error:
        raise AudioAssemblyError("Không chạy được FFmpeg trong bản ứng dụng.") from error


def concat_mp3_files(file_list: List[Path], output_file: Path, *, cancel_event=None, timeout=180) -> Path:
    """
    Remux through FFmpeg, including a single chunk, to reject invalid audio.
    """
    output_file = Path(output_file).resolve()
    if hasattr(sys, 'getandroidapilevel'):
        from java import jclass
        if not file_list or any(not Path(p).is_file() for p in file_list):
            raise AudioAssemblyError('Thiếu phân đoạn âm thanh để ghép.')
        output_file.parent.mkdir(parents=True, exist_ok=True)
        result = jclass('com.everaistudio.mobile.NativeBridge').merge(
            [str(Path(p).resolve()) for p in file_list], str(output_file))
        if not result or not output_file.is_file() or not output_file.stat().st_size:
            raise AudioAssemblyError('Không ghép được âm thanh trên Android.')
        return output_file
    output_file.parent.mkdir(parents=True, exist_ok=True)

    existing_files = [Path(p).resolve() for p in file_list if Path(p).exists()]
    if not existing_files or len(existing_files) != len(file_list):
        raise ValueError("Không có file âm thanh phân đoạn nào tồn tại để ghép nối.")

    ffmpeg_bin = ensure_ffmpeg()

    if ffmpeg_bin:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            for p in existing_files:
                safe_path = str(p.resolve()).replace("\\", "/")
                safe_path = safe_path.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
            list_file_path = f.name

        try:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file_path,
                "-c",
                "copy",
                str(output_file),
            ]
            try:
                res = run_audio_command(cmd, cancel_event=cancel_event, timeout=timeout, text=True)
            except OSError as exc:
                raise AudioAssemblyError("Không chạy được FFmpeg trong bản ứng dụng.") from exc
            if res.returncode == 0 and output_file.exists() and output_file.stat().st_size > 0:
                return output_file
            raise AudioAssemblyError("Không ghép được âm thanh. Kiểm tra FFmpeg hoặc chọn nơi lưu khác.")
        finally:
            if os.path.exists(list_file_path):
                try:
                    os.remove(list_file_path)
                except Exception:
                    pass

    raise AudioAssemblyError("Thiếu FFmpeg. Vui lòng dùng bản phát hành đầy đủ hoặc cài FFmpeg.")


# =============================================================================
# 4. NETWORK & FORMATTING HELPERS
# =============================================================================

def format_bytes(num_bytes: int) -> str:
    """Formats byte counts into clean human-readable strings (B, KB, MB, GB)."""
    try:
        val = float(num_bytes)
    except (TypeError, ValueError):
        val = 0.0

    if val < 1024:
        return f"{int(val)} B"
    elif val < 1024 * 1024:
        return f"{val / 1024:.2f} KB"
    elif val < 1024 * 1024 * 1024:
        return f"{val / (1024 * 1024):.2f} MB"
    else:
        return f"{val / (1024 * 1024 * 1024):.2f} GB"


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def download_file(url: str, destination: Path, expected_sha256=None):
    """Publish a build asset only after a complete transfer and optional digest check."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".download")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "EverAI-build"})
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(request, timeout=60) as response:
            while block := response.read(256 * 1024):
                output.write(block)
                digest.update(block)
                size += len(block)
            expected_size = response.headers.get("Content-Length")
            if expected_size is not None and size != int(expected_size):
                raise OSError("Incomplete download; the previous asset was preserved")
        if expected_sha256 and digest.hexdigest() != expected_sha256:
            raise ValueError("Downloaded asset SHA256 mismatch")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return digest.hexdigest()


def read_json(path: Path, default=None):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def writable_directory(path: Path) -> Path:
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as probe:
        # Creating an empty file can succeed on a full disk; verify a real write.
        probe.write(b"\0" * 4096)
        probe.flush()
        os.fsync(probe.fileno())
    return path


def application_data_dir() -> Path:
    if hasattr(sys, 'getandroidapilevel'):
        from java import jclass
        return writable_directory(Path(str(jclass('com.everaistudio.mobile.NativeBridge').dataDir())))
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    # Explicit portable mode must not silently fall back when this path is read-only.
    if sys.platform == "darwin":
        default = Path.home() / "Library" / "Application Support" / "EverAIStudio"
    else:
        default = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "EverAIStudio"
    target = base / "EverAIStudio-data" if (base / "portable.flag").exists() else default
    return writable_directory(target)


def documents_directory() -> Path:
    if sys.platform == "win32":
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value)
    return Path.home() / "Documents"


def normalized_search(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
                   if unicodedata.category(c) != "Mn")


def audio_filename(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    name = " ".join(name.split()).strip(" .")[:80].rstrip(" .") or "Voice Pro audio"
    if name.lower().endswith(".mp3"):
        name = name[:-4].rstrip(" .") or "Voice Pro audio"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        name = "Audio " + name
    return name + ".mp3"


def publish_audio(temporary: Path, destination: Path) -> Path:
    """Reserve the final name exclusively so even another app instance cannot overwrite it."""
    destination = Path(destination)
    for index in range(10000):
        candidate = destination if index == 0 else destination.with_name(f"{destination.stem} ({index}){destination.suffix}")
        try:
            with candidate.open("xb"):
                pass
            os.replace(temporary, candidate)
            return candidate
        except FileExistsError:
            continue
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
    raise OSError("Có quá nhiều file trùng tên. Hãy chọn tên khác.")


def redact_log(value: str) -> str:
    value = str(value)
    # URLs may contain signed paths as well as query credentials; remove them entirely.
    value = re.sub(r"https?://[^\s'\"<>]+", "[URL đã ẩn]", value)
    value = re.sub(r"\b[^\s:@]+:[^\s@]+@[^\s]+", "[Proxy đã ẩn]", value)
    value = re.sub(r"\b[\w.-]+:\d+:[^\s:]+:[^\s]+", "[Proxy đã ẩn]", value)
    value = re.sub(r"(?i)((?:sessionid|csrftoken|csrf.?token|cookie|authorization|password|api[_-]?key|token)\s*['\"]?\s*[:=]\s*)[^\n,;]+", r"\1[đã ẩn]", value)
    return value


def password_hash(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200000).hex()
    return salt + ":" + digest


def verify_password(password: str, encoded: str) -> bool:
    import hmac
    try:
        salt, _ = encoded.split(":", 1)
        return bool(password) and hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


class StudioStore:
    """Small JSON store shared by source and frozen launchers."""
    def __init__(self, root: Path):
        self.root = writable_directory(root)
        self.warnings = []
        defaults = dict(voice_id="vi_male_leduc_mb", model_id="everai-v1.6", speed=1.0,
                        pitch=1.0, bitrate="128 kbps", output_dir=str(documents_directory() / "Voice Pro Audio"),
                        randomize_device=True, download_via_direct_ip=True, concurrency=3, srt_workers=5,
                        use_proxy=True, admin_hash="", tts_provider="everai", tts_language='Tất cả')
        loaded = self.load("settings.json", {})
        self.settings = defaults.copy()
        if isinstance(loaded, dict):
            for key, default in defaults.items():
                value = loaded.get(key, default)
                if isinstance(value, type(default)):
                    self.settings[key] = value
            for key in ("speed", "pitch"):
                if not 0.5 <= self.settings[key] <= 2:
                    self.settings[key] = 1.0
            if not 1 <= self.settings["concurrency"] <= 8:
                self.settings["concurrency"] = 3
            if not 1 <= self.settings["srt_workers"] <= 8:
                self.settings["srt_workers"] = 5
            if self.settings["bitrate"] not in ("128 kbps", "192 kbps", "256 kbps"):
                self.settings["bitrate"] = "128 kbps"
            if self.settings["model_id"] not in ("everai-v1.5", "everai-v1.6"):
                self.settings["model_id"] = "everai-v1.6"
        self.settings["use_proxy"] = True
        self.recent = self._load_recent()
        self.proxy_traffic = self._load_proxy_traffic()
        proxies = self.root / "proxies.json"
        if not proxies.exists():
            legacy = Path(__file__).resolve().parent / "data" / "everai_proxies.json"
            if not getattr(sys, "frozen", False) and legacy.is_file():
                shutil.copyfile(legacy, proxies)
                if not loaded:
                    migrated = self.load("proxies.json", {})
                    self.settings["use_proxy"] = bool(migrated.get("items"))
            else:
                atomic_json(proxies, {"version": 1, "items": []})

    def load(self, name, default=None):
        path = self.root / name
        try:
            return read_json(path, default)
        except (ValueError, UnicodeError):
            backup = path.with_name(path.name + f".corrupt-{time.time_ns()}")
            shutil.copyfile(path, backup)
            self.warnings.append(f"Không đọc được {name}; đã giữ bản sao và dùng mặc định.")
            return default

    def save_settings(self):
        atomic_json(self.root / "settings.json", self.settings)

    @contextmanager
    def _update_lock(self):
        # SQLite supplies a process/thread lock; the existing JSON files remain the data source.
        db = None
        try:
            db = sqlite3.connect(self.root / "store-lock.sqlite3", timeout=15, isolation_level=None)
            db.execute("BEGIN IMMEDIATE")
            yield
        except sqlite3.Error as exc:
            raise OSError("Không khóa được dữ liệu dùng chung. Kiểm tra nơi lưu và thử lại.") from exc
        finally:
            if db is not None:
                db.close()

    def _load_recent(self):
        history = self.load("recent.json", [])
        return [p for p in history if isinstance(p, dict) and isinstance(p.get("path"), str)][:10] if isinstance(history, list) else []

    def _load_proxy_traffic(self):
        raw_traffic = self.load("proxy_traffic.json", {})
        traffic = {}
        if isinstance(raw_traffic, dict) and isinstance(raw_traffic.get("items"), list):
            for item in raw_traffic["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("proxy_id"), str):
                    continue
                try:
                    traffic[item["proxy_id"]] = {
                        "proxy_id": item["proxy_id"],
                        "name": str(item.get("name") or "Proxy"),
                        "address": str(item.get("address") or ""),
                        "tx_bytes": max(0, int(item.get("tx_bytes", 0))),
                        "rx_bytes": max(0, int(item.get("rx_bytes", 0))),
                        "request_count": max(0, int(item.get("request_count", 0))),
                        "measurement": str(item.get("measurement") or "unknown"),
                        "last_used_at": float(item.get("last_used_at", 0) or 0),
                    }
                except (TypeError, ValueError):
                    continue
        return traffic

    def add_result(self, path, *, project_id=None, revision=None, format=None):
        path = str(Path(path).resolve())
        item = dict(path=path, time=time.strftime("%d/%m/%Y %H:%M"))
        if project_id is not None:
            item.update(project_id=project_id, revision=revision, format=format)
        with self._update_lock():
            recent = ([item]
                      + [entry for entry in self._load_recent() if entry["path"] != path])[:10]
            atomic_json(self.root / "recent.json", recent)
            self.recent = recent

    def remove_result(self, path):
        path = str(Path(path).resolve())
        with self._update_lock():
            recent = [entry for entry in self._load_recent() if entry["path"] != path]
            atomic_json(self.root / "recent.json", recent)
            self.recent = recent

    def proxy_traffic_entries(self):
        """Returns a copy of persistent per-proxy byte counters."""
        self.proxy_traffic = self._load_proxy_traffic()
        return [dict(item) for item in self.proxy_traffic.values()]

    def record_proxy_traffic(self, proxy_id, name, address, stats):
        """Adds one completed client-attempt's counters without storing credentials."""
        if not isinstance(proxy_id, str) or not proxy_id:
            return
        try:
            tx = max(0, int(stats.get("proxy_tx_bytes", 0) or 0))
            rx = max(0, int(stats.get("proxy_rx_bytes", 0) or 0))
            requests = max(0, int(stats.get("proxy_request_count", 0) or 0))
        except (AttributeError, TypeError, ValueError):
            return
        parsed = parse_proxy(address) if isinstance(address, str) else None
        safe_address = f"{parsed.host}:{parsed.port}" if parsed else str(address or "")
        with self._update_lock():
            traffic = self._load_proxy_traffic()
            previous = traffic.get(proxy_id, {
                "proxy_id": proxy_id, "name": "Proxy", "address": "",
                "tx_bytes": 0, "rx_bytes": 0, "request_count": 0,
                "measurement": "unknown", "last_used_at": 0,
            })
            previous.update({
                "name": str(name or previous.get("name") or "Proxy"),
                "address": safe_address or str(previous.get("address") or ""),
                "tx_bytes": max(0, int(previous.get("tx_bytes", 0))) + tx,
                "rx_bytes": max(0, int(previous.get("rx_bytes", 0))) + rx,
                "request_count": max(0, int(previous.get("request_count", 0))) + requests,
                "measurement": str(stats.get("measurement") or previous.get("measurement") or "unknown"),
                "last_used_at": time.time(),
            })
            traffic[proxy_id] = previous
            atomic_json(self.root / "proxy_traffic.json", {
                "version": 1, "items": list(traffic.values()), "updated_at": time.time(),
            })
            self.proxy_traffic = traffic

    def reset_proxy_traffic(self):
        with self._update_lock():
            atomic_json(self.root / "proxy_traffic.json", {"version": 1, "items": [], "updated_at": time.time()})
            self.proxy_traffic = {}

    def unfinished_jobs(self):
        jobs = []
        for path in (self.root / "jobs").glob("*/manifest.json"):
            try:
                item = read_json(path)
                if item.get("status") != "completed" and item.get("text") and isinstance(item.get("config"), dict):
                    jobs.append((path.parent, item))
            except (OSError, ValueError, TypeError, AttributeError):
                self.warnings.append("Có tác vụ cũ không đọc được; dữ liệu được giữ lại.")
        return sorted(jobs, key=lambda pair: pair[1].get("created_at", 0), reverse=True)
