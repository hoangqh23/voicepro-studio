"""
EverAI TTS Engine - Long-Text Processing, Proxy Rotation, and Audio Stitching.
"""

import os
import time
import logging
import threading
import uuid
import hashlib
import json
import tempfile
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from .client import EverAIClient, EverAIError, EverAIQuotaError, EverAIConnectionError, EverAICancelled
    from .device_profiles import get_random_device_profile, DeviceProfile
    from .license_manager import LicenseManager
    from .utils import normalize_tts_text
    from .utils import split_text_into_chunks, concat_mp3_files, format_bytes, atomic_json, read_json, publish_audio, redact_log, parse_proxy
except ImportError:
    from client import EverAIClient, EverAIError, EverAIQuotaError, EverAIConnectionError, EverAICancelled
    from device_profiles import get_random_device_profile, DeviceProfile
    from license_manager import LicenseManager
    from utils import normalize_tts_text
    from utils import split_text_into_chunks, concat_mp3_files, format_bytes, atomic_json, read_json, publish_audio, redact_log, parse_proxy

logger = logging.getLogger("everai_engine")


class EverAITTSEngine:
    """
    High-level engine that manages:
    1. Long text chunking (< 450 chars).
    2. Per-chunk synthesis with proxy rotation & retries upon 429 quota.
    3. Multithreaded concurrent synthesis (default 3 concurrent workers) with strict sequential audio ordering.
    4. Final audio stitching using FFmpeg.
    5. Comprehensive real-time logging & wire bandwidth metering.
    """

    def __init__(
        self,
        proxies: Optional[List[str]] = None,
        proxy_catalog: Optional[Any] = None,
        session_id: Optional[str] = None,
        max_chunk_chars: int = 450,
        max_retries_per_chunk: int = 5,
        retry_delay_sec: float = 3.0,
        temp_dir: Optional[Path] = None,
        randomize_device: bool = True,
        download_via_direct_ip: bool = True,
        concurrency: int = 3,
        log_callback: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[int, int, float, str], None]] = None,
        status_callback: Optional[Callable[[str, dict], None]] = None,
        license_manager=None,
    ):
        """
        :param proxies: List of proxy strings e.g. ["160.250.x.x:port", ...]
        :param proxy_catalog: Optional ProxyCatalog instance
        :param session_id: Optional EverAI sessionid cookie
        :param max_chunk_chars: Maximum characters per chunk (EverAI hard limit is 500)
        :param max_retries_per_chunk: Number of retries on 429/connection error
        :param retry_delay_sec: Delay between retries
        :param temp_dir: Directory for temporary chunk audio files
        :param randomize_device: If True, generates a fresh random device profile per chunk/attempt
        :param download_via_direct_ip: If True, downloads MP3 files directly via machine IP (no proxy)
        :param concurrency: Number of concurrent chunks processed in parallel (default: 3)
        :param log_callback: func(msg, level)
        :param progress_callback: func(current_chunk, total_chunks, percent, status_text)
        """
        self.proxies = [p.strip() for p in (proxies or []) if p and p.strip()]
        self.proxy_catalog = proxy_catalog
        self.proxy_index = 0
        self.session_id = session_id
        self.max_chunk_chars = min(max_chunk_chars, 480)
        self.max_retries = max_retries_per_chunk
        self.retry_delay = retry_delay_sec
        self.temp_dir = Path(temp_dir or "output/temp_chunks").resolve()
        self.randomize_device = randomize_device
        self.download_via_direct_ip = download_via_direct_ip
        self.concurrency = max(1, int(concurrency))
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.is_cancelled = False
        self.cancel_event = threading.Event()
        self.status_callback = status_callback
        self.license_manager = license_manager

        # Concurrency & Synchronization locks
        self._traffic_lock = threading.RLock()
        self._proxy_lock = threading.RLock()
        self._progress_lock = threading.RLock()
        self._last_rotate_time = 0.0

        # Cumulative Wire Bandwidth / Traffic Tracking
        self.total_proxy_tx_bytes = 0
        self.total_proxy_rx_bytes = 0
        self.total_direct_tx_bytes = 0
        self.total_direct_rx_bytes = 0
        self._proxy_traffic = {}
        self._measurement_methods = set()
        self.shared_csrf_token = None
        self._csrf_lock = threading.Lock()

    def get_traffic_stats(self) -> Dict[str, Any]:
        """Returns cumulative traffic statistics across all chunks in bytes."""
        with self._traffic_lock:
            p_tx = self.total_proxy_tx_bytes
            p_rx = self.total_proxy_rx_bytes
            d_tx = self.total_direct_tx_bytes
            d_rx = self.total_direct_rx_bytes
            proxy_requests = sum(item.get("request_count", 0) for item in self._proxy_traffic.values())
            methods = set(self._measurement_methods)
        return {
            "proxy_tx_bytes": p_tx,
            "proxy_rx_bytes": p_rx,
            "proxy_total_bytes": p_tx + p_rx,
            "direct_tx_bytes": d_tx,
            "direct_rx_bytes": d_rx,
            "direct_total_bytes": d_tx + d_rx,
            "total_bytes": p_tx + p_rx + d_tx + d_rx,
            "proxy_request_count": proxy_requests,
            "measurement": ("curl-http-transfer" if methods == {"curl-http-transfer"}
                             else "payload-fallback" if methods else "none"),
        }

    def get_proxy_traffic_stats(self) -> List[Dict[str, Any]]:
        """Returns per-proxy counters accumulated by this engine instance."""
        with self._traffic_lock:
            return [dict(item) for item in self._proxy_traffic.values()]

    def _proxy_context(self, address: Optional[str] = None) -> Optional[Dict[str, str]]:
        """Captures proxy identity before an attempt, so rotation cannot misattribute it."""
        if self.proxy_catalog:
            active = self.proxy_catalog.get_active_proxy()
            if active and active.id == "builtin-default":
                return None
            if active and active.address:
                return {"proxy_id": active.id, "name": active.name, "address": self._proxy_address_hint(active.address)}
        if address:
            parsed = parse_proxy(address)
            hint = f"{parsed.host}:{parsed.port}" if parsed else "proxy"
            import hashlib
            return {"proxy_id": "address:" + hashlib.sha256(address.encode("utf-8")).hexdigest()[:24],
                    "name": hint, "address": hint}
        return None

    @staticmethod
    def _proxy_address_hint(address: str) -> str:
        parsed = parse_proxy(address)
        return f"{parsed.host}:{parsed.port}" if parsed else "proxy"


    def _accumulate_client_traffic(self, context: Optional[Dict[str, str]], stats: Dict[str, Any]) -> None:
        if not isinstance(stats, dict):
            return
        p_tx = int(stats.get("proxy_tx_bytes", 0) or 0)
        p_rx = int(stats.get("proxy_rx_bytes", 0) or 0)
        d_tx = int(stats.get("direct_tx_bytes", 0) or 0)
        d_rx = int(stats.get("direct_rx_bytes", 0) or 0)
        method = str(stats.get("measurement") or "unknown")
        requests = int(stats.get("proxy_request_count", 0) or 0)
        with self._traffic_lock:
            self.total_proxy_tx_bytes += p_tx
            self.total_proxy_rx_bytes += p_rx
            self.total_direct_tx_bytes += d_tx
            self.total_direct_rx_bytes += d_rx
            self._measurement_methods.add(method)
            if context and (p_tx or p_rx or requests):
                entry = self._proxy_traffic.setdefault(context["proxy_id"], {
                    "proxy_id": context["proxy_id"], "name": context["name"],
                    "address": context["address"], "tx_bytes": 0, "rx_bytes": 0,
                    "request_count": 0, "measurement": method,
                })
                entry["tx_bytes"] += p_tx
                entry["rx_bytes"] += p_rx
                entry["request_count"] += requests
                entry["measurement"] = method

    def log(self, message: str, level: str = "INFO") -> None:
        """Logs message to standard logger and UI callback."""
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

    def update_progress(self, current: int, total: int, percent: float, text: str) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(current, total, percent, text)
            except Exception:
                pass

    def cancel(self) -> None:
        """Requests cancellation of ongoing processing."""
        self.is_cancelled = True
        self.cancel_event.set()
        self.log("[ENGINE] Nhận lệnh dừng từ người dùng. Đang hủy tiến trình...", "WARNING")

    def get_current_proxy(self) -> Optional[str]:
        with self._proxy_lock:
            if self.proxy_catalog:
                active = self.proxy_catalog.get_active_proxy()
                if active and active.address:
                    return active.address
            if not self.proxies:
                return None
            return self.proxies[self.proxy_index % len(self.proxies)]

    def get_proxy_for_chunk(self, idx: int = 1) -> Optional[str]:
        """Use only the explicitly active catalog route (or its builtin fallback)."""
        with self._proxy_lock:
            if self.proxy_catalog:
                active = self.proxy_catalog.get_active_proxy()
                if active and active.address:
                    return active.address
            if self.proxies:
                return self.proxies[(self.proxy_index + idx - 1) % len(self.proxies)]
            return None

    def rotate_proxy(self, force_rotate: bool = False) -> Optional[str]:
        with self._csrf_lock:
            self.shared_csrf_token = None
        with self._proxy_lock:
            if self.proxy_catalog:
                active = self.proxy_catalog.get_active_proxy()
                if active and active.rotate_url and (active.rotate_on_429 or force_rotate):
                    now = time.time()
                    if not force_rotate and (now - self._last_rotate_time < 3.0):
                        return active.address
                    self._last_rotate_time = now
                    self.log(f"[ENGINE] 🔄 Kích hoạt link xoay IP cho proxy '{active.name}'...", "INFO")
                    res = self.proxy_catalog.rotate_proxy(active, wait_after_sec=5, cancel_event=self.cancel_event)
                    if res.get("ok"):
                        self.log(f"[ENGINE] ✓ Xoay IP thành công qua API link (IP mới: {res.get('ip')})", "SUCCESS")
                        return active.address
                    else:
                        self.log(f"[ENGINE] ⚠️ Link xoay IP thất bại ({res.get('error')})", "WARNING")

                if active:
                    return active.address

            if self.proxies:
                self.proxy_index = (self.proxy_index + 1) % len(self.proxies)
                new_proxy = self.get_current_proxy()
                self.log(f"[ENGINE] Đã xoay sang proxy #{self.proxy_index + 1}/{len(self.proxies)}: {new_proxy}", "INFO")
                return new_proxy

            return None

    def split_text(self, text: str) -> List[str]:
        """Splits full text into safe chunks under max_chunk_chars."""
        text = normalize_tts_text(text)
        chunks = split_text_into_chunks(text, max_length=self.max_chunk_chars)
        self.log(
            f"[ENGINE] Phân tích văn bản: {len(text):,} ký tự -> chia thành {len(chunks)} phân đoạn an toàn (tối đa {self.max_chunk_chars} ký tự/đoạn)",
            "INFO",
        )
        return chunks

    def _process_single_chunk(
        self,
        idx: int,
        chunk_text: str,
        total_chunks: int,
        output_chunk_path: Path,
        speaker_id: str,
        model_id: str,
        speed: float,
        pitch: float,
        volume: int,
        bitrate: str,
        audio_type: str,
        has_configured_proxies: bool,
    ) -> Path:
        chunk_filename = output_chunk_path
        success = False
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            if self.is_cancelled:
                raise EverAICancelled("Đã dừng tác vụ.")
            if self.license_manager is not None:
                self.license_manager.ensure_active()

            current_proxy = self.get_proxy_for_chunk(idx)
            proxy_context = self._proxy_context(current_proxy)

            # CRITICAL: If proxies are configured, NEVER allow fallback to direct machine IP!
            if has_configured_proxies and not current_proxy:
                self.log(
                    f"[ENGINE] 🚫 CHẶN RÒ RỈ IP [Đoạn {idx}]: Cấu hình yêu cầu Proxy nhưng chưa có IP Proxy sẵn sàng. "
                    f"Đang delay {self.retry_delay}s và xoay IP (Tuyệt đối KHÔNG dùng IP máy)...",
                    "WARNING",
                )
                self._wait_retry(attempt)
                if attempt < self.max_retries:
                    self.rotate_proxy(force_rotate=True)
                continue

            device_prof = get_random_device_profile() if self.randomize_device else None
            dev_info = f" | Thiết bị: {device_prof.name}" if device_prof else ""
            self.log(
                f"[ENGINE] Đoạn {idx}/{total_chunks} - Thử lần {attempt}/{self.max_retries} | Proxy: {current_proxy or 'Trực tiếp'}{dev_info}",
                "INFO",
            )

            # Khi randomize_device bật (mặc định), không dùng chung CSRF token để mỗi chunk
            # có phiên riêng biệt gắn liền với fingerprint và IP mới đó.
            chunk_csrf = None if self.randomize_device else self.shared_csrf_token

            client = EverAIClient(
                proxy=current_proxy,
                session_id=self.session_id,
                device_profile=device_prof,
                download_via_direct_ip=self.download_via_direct_ip,
                csrf_token=chunk_csrf,
                log_callback=self.log_callback,
            )
            client.cancel_event = self.cancel_event
            client.prefer_csrf_head = getattr(self, 'prefer_csrf_head', False)

            try:
                audio_bytes = client.synthesize_one_chunk(
                    text=chunk_text,
                    speaker_id=speaker_id,
                    model_id=model_id,
                    speed=speed,
                    pitch=pitch,
                    volume=volume,
                    bitrate=bitrate,
                    audio_type=audio_type,
                )
                if client.csrf_token and not self.shared_csrf_token and not self.randomize_device:
                    with self._csrf_lock:
                        self.shared_csrf_token = client.csrf_token

                # Publish a complete chunk before its character charge can be committed.
                descriptor, temporary = tempfile.mkstemp(dir=chunk_filename.parent, suffix=".part")
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(audio_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, chunk_filename)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                success = True

                c_stats = client.get_traffic_stats() if hasattr(client, "get_traffic_stats") else None
                if isinstance(c_stats, dict):
                    self._accumulate_client_traffic(proxy_context, c_stats)
                    p_str = format_bytes(c_stats.get("proxy_total_bytes", 0))
                    d_str = format_bytes(c_stats.get("direct_total_bytes", 0))
                else:
                    p_str = "0 B"
                    d_str = "0 B"

                self.log(
                    f"[ENGINE] ✓ Đoạn {idx}/{total_chunks} thành công! Lưu tạm: {chunk_filename.name} "
                    f"[Data Proxy: {p_str} | Tải audio mạng máy: {d_str}]",
                    "INFO",
                )
                return chunk_filename

            except EverAICancelled:
                raise
            except EverAIQuotaError as eq:
                last_error = eq
                self.log(f"[ENGINE] Hạn mức dịch vụ trên đoạn {idx}", "WARNING")
                if has_configured_proxies:
                    self.log(f"[ENGINE] 🔄 Đang delay {self.retry_delay}s rồi tự động xoay IP (Bảo vệ IP máy)...", "INFO")
                    self._wait_retry(attempt)
                    if attempt < self.max_retries:
                        self.rotate_proxy()
                else:
                    self.log(
                        "[ENGINE] Dịch vụ đang giới hạn chuyển đổi. Kiểm tra tài khoản hoặc thử lại sau.",
                        "ERROR",
                    )
                    self._wait_retry(attempt)

            except (EverAIConnectionError, EverAIError) as e:
                with self._csrf_lock:
                    self.shared_csrf_token = None
                last_error = e
                err_str = str(e)
                self.log(f"[ENGINE] Lỗi xử lý đoạn {idx} (lần {attempt}/{self.max_retries}): {type(e).__name__}", "WARNING")
                if has_configured_proxies:
                    is_timeout = any(k in err_str.lower() for k in ["timed out", "timeout", "read timed out", "connection", "proxy"])
                    if is_timeout:
                        self.log(
                            f"[ENGINE] Lỗi kết nối trên đoạn {idx}. "
                            f"Đang delay {self.retry_delay}s rồi tự động xoay IP (TUYỆT ĐỐI KHÔNG dùng IP máy trần)...",
                            "WARNING",
                        )
                    else:
                        self.log(
                            f"[ENGINE] 🔄 Lỗi mạng/proxy trên đoạn {idx}. Đang delay {self.retry_delay}s và xoay IP (Chặn dùng IP máy)...",
                            "INFO",
                        )
                    self._wait_retry(attempt)
                    if attempt < self.max_retries:
                        self.rotate_proxy(force_rotate=True)
                else:
                    self._wait_retry(attempt)

            except Exception as e:
                last_error = e
                self.log(f"[ENGINE] Lỗi bất ngờ trên đoạn {idx}: {type(e).__name__}", "ERROR")
                if has_configured_proxies:
                    self.log(f"[ENGINE] Đang delay {self.retry_delay}s và xoay IP (Bảo vệ IP máy)...", "INFO")
                    self._wait_retry(attempt)
                    if attempt < self.max_retries:
                        self.rotate_proxy(force_rotate=True)
                else:
                    self._wait_retry(attempt)

            finally:
                if not success and hasattr(client, "get_traffic_stats"):
                    c_stats = client.get_traffic_stats()
                    if isinstance(c_stats, dict):
                        self._accumulate_client_traffic(proxy_context, c_stats)
                if hasattr(client, "close"):
                    client.close()

        if not success:
            if last_error:
                raise last_error
            if has_configured_proxies:
                raise EverAIError(
                    f"Thất bại khi xử lý đoạn {idx}/{total_chunks} sau {self.max_retries} lần thử qua Proxy. "
                    "Tiến trình đã dừng để BẢO VỆ AN TOÀN (Tuyệt đối không dùng IP máy thật). "
                    "Vui lòng kiểm tra lại đường truyền proxy hoặc link xoay IP."
                )
            else:
                raise EverAIError(
                    f"Thất bại khi xử lý đoạn {idx}/{total_chunks} sau {self.max_retries} lần thử. "
                    "Vui lòng kiểm tra lại kết nối mạng."
                )

    def _status(self, kind, **details):
        if self.status_callback:
            self.status_callback(kind, details)

    def _wait_retry(self, attempt):
        if attempt >= self.max_retries:
            return
        self._status("retrying")
        if self.cancel_event.wait(self.retry_delay):
            raise EverAICancelled("Đã dừng tác vụ.")

    def process(
        self, text: str, output_file: Path,
        speaker_id: str = "vi_male_leduc_mb", model_id: str = "everai-v1.6",
        speed: float = 1.0, pitch: float = 1.0, volume: int = 100,
        bitrate: str = "128 kbps", audio_type: str = "mp3",
        keep_temp_chunks: bool = True, job_dir: Optional[Path] = None,
    ) -> Path:
        """Generate in parallel; a job directory enables durable, parameter-bound recovery."""
        if self.cancel_event.is_set():
            raise EverAICancelled("Đã dừng tác vụ.")
        output_file = Path(output_file).resolve()
        chunks = self.split_text(text)
        if not chunks:
            raise EverAIError("Văn bản đầu vào trống.")
        if any(len(chunk) > 500 for chunk in chunks):
            raise EverAIError("Phân đoạn vượt giới hạn dịch vụ.")
        config = dict(speaker_id=speaker_id, model_id=model_id, speed=speed, pitch=pitch,
                      volume=volume, bitrate=bitrate, audio_type=audio_type)
        identity = dict(text=text, config=config, chunk_version=2, max_chunk_chars=self.max_chunk_chars)
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        root = Path(job_dir).resolve() if job_dir else self.temp_dir / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "manifest.json"
        manifest = read_json(manifest_path) if job_dir else None
        if manifest and manifest.get("fingerprint") != fingerprint:
            raise EverAIError("Nội dung hoặc giọng đã thay đổi. Hãy tạo tác vụ mới.")
        if not manifest:
            manifest = dict(identity, fingerprint=fingerprint, completed={}, status="pending",
                            output_file=str(output_file), created_at=time.time())
        if manifest.get("status") == "completed":
            final = Path(manifest.get("result", ""))
            if final.is_file():
                return final
            raise EverAIError("Kết quả cũ đã bị di chuyển. Hãy tạo tác vụ mới.")
        self._status("preparing")
        manifest["output_file"] = str(output_file)
        manifest["status"] = "running"
        results = [None] * len(chunks)
        for key, entry in list(manifest["completed"].items()):
            index = int(key) - 1
            path = root / entry["file"]
            if (0 <= index < len(chunks) and path.parent == root and path.is_file()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]):
                results[index] = path
            else:
                del manifest["completed"][key]

        def persist():
            atomic_json(manifest_path, manifest)

        manager = self.license_manager or LicenseManager()
        self.license_manager = manager
        job_id = hashlib.sha256(str(root).encode()).hexdigest()
        paths = [root / f"chunk_{i:03d}_{fingerprint[:8]}.mp3" for i in range(1, len(chunks)+1)]
        with manager.job(job_id, fingerprint, chunks, paths,
                         existing={i+1 for i, result in enumerate(results) if result is not None}):
            for index, saved_path, digest in manager.recovered_chunks(job_id):
                path = Path(saved_path)
                if (path == paths[index-1] and path.is_file()
                        and hashlib.sha256(path.read_bytes()).hexdigest() == digest):
                    results[index-1] = path
                    manifest["completed"][str(index)] = dict(file=path.name, sha256=digest)
            manifest["license_job_id"] = job_id
            persist()
            return self._execute_job(chunks, results, root, fingerprint, manifest, persist, output_file,
                                     job_dir, keep_temp_chunks, manager, job_id, config)

    def _execute_job(self, chunks, results, root, fingerprint, manifest, persist, output_file,
                     job_dir, keep_temp_chunks, manager, job_id, config):
        lock = threading.Lock()
        configured = bool(self.proxies or (self.proxy_catalog and self.proxy_catalog.get_active_proxy()))

        def synthesize(index, chunk):
            manager.ensure_active()
            path = root / f"chunk_{index:03d}_{fingerprint[:8]}.mp3"
            result = self._process_single_chunk(
                index, chunk, len(chunks), path,
                config["speaker_id"], config["model_id"], config["speed"], config["pitch"],
                config["volume"], config["bitrate"], config["audio_type"], configured)
            if not result.is_file() or not result.stat().st_size:
                raise EverAIError("Phân đoạn âm thanh rỗng.")
            with lock:
                manager.commit_chunk(job_id, index, result)
                results[index - 1] = result
                manifest["completed"][str(index)] = dict(
                    file=result.name, sha256=hashlib.sha256(result.read_bytes()).hexdigest())
                persist()
                done = sum(item is not None for item in results)
                self.update_progress(done, len(chunks), done / len(chunks) * 90,
                                     f"Đã tạo {done}/{len(chunks)} đoạn")
                self._status("generating", completed=done, total=len(chunks))
            return result

        error = None
        try:
            completed = sum(p is not None for p in results)
            self.update_progress(completed, len(chunks), completed / len(chunks) * 90,
                                 f"Đã tạo {completed}/{len(chunks)} đoạn")
            self._status("generating", completed=completed, total=len(chunks))
            with ThreadPoolExecutor(max_workers=min(self.concurrency, len(chunks))) as executor:
                futures = [executor.submit(synthesize, i, chunk)
                           for i, chunk in enumerate(chunks, 1) if results[i - 1] is None]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        if error is None:
                            error = exc
                        self.cancel()
                        for pending in futures:
                            pending.cancel()
                # Exiting the executor waits for every worker before another job can start.
            if error:
                raise error
            if self.is_cancelled or self.cancel_event.is_set():
                raise EverAICancelled("Đã dừng tác vụ.")
            self._status("merging")
            self.update_progress(len(chunks), len(chunks), 95, "Đang ghép âm thanh")
            output_file.parent.mkdir(parents=True, exist_ok=True)
            target = output_file.with_name(f".everai-{uuid.uuid4().hex}.mp3") if job_dir else output_file
            try:
                final = self._assemble_audio(results, target)
                if not final.is_file() or not final.stat().st_size:
                    raise EverAIError("Không có file âm thanh hoàn chỉnh.")
                if self.cancel_event.is_set():
                    raise EverAICancelled("Đã dừng tác vụ.")
                if job_dir:
                    final = publish_audio(final, output_file)
            finally:
                if job_dir:
                    target.unlink(missing_ok=True)
            manifest.update(status="completed", result=str(final))
            persist()
        except Exception as exc:
            manifest["status"] = "interrupted"
            # Preserve the synthesis error if the disk also became unavailable.
            try:
                persist()
            except OSError:
                self.log("Không lưu được trạng thái tác vụ.", "ERROR")
            self._status("interrupted")
            raise
        if job_dir or not keep_temp_chunks:
            for path in results:
                path.unlink(missing_ok=True)
        self.update_progress(len(chunks), len(chunks), 100, "Đã lưu file")
        self._status("completed", file=str(final))
        return final

    def _assemble_audio(self, results, target):
        return concat_mp3_files(results, target, cancel_event=self.cancel_event)
