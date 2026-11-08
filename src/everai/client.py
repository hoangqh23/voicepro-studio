"""
EverAI HTTP API Client with Comprehensive Logging, TLS Impersonation (curl_cffi),
and Quota Detection.
"""

import time
import logging
import threading
import sys
from typing import Optional, Dict, Any, Callable
import requests

if hasattr(sys, 'getandroidapilevel'):
    from . import android_transport as cffi_requests
    from .android_transport import RequestException as CffiRequestException
    CURL_CFFI_AVAILABLE = True
else:
    try:
        from curl_cffi import requests as cffi_requests
        from curl_cffi.requests.exceptions import RequestException as CffiRequestException
        CURL_CFFI_AVAILABLE = True
    except ImportError:
        import requests as cffi_requests
        from requests.exceptions import RequestException as CffiRequestException
        CURL_CFFI_AVAILABLE = False

REQUEST_EXCEPTIONS = (requests.RequestException, CffiRequestException)

logger = logging.getLogger("everai_client")
try:
    from .utils import redact_log
except ImportError:
    from utils import redact_log


class ImpersonatedSession(cffi_requests.Session if CURL_CFFI_AVAILABLE else requests.Session):
    """
    Session wrapper ensuring seamless HTTP/2, JA3/JA4 TLS fingerprint impersonation,
    exact C-level byte accounting, and full compatibility with telemetry hooks.
    """
    def __init__(self, impersonate: Optional[str] = None, *args, **kwargs):
        if CURL_CFFI_AVAILABLE and impersonate:
            kwargs["impersonate"] = impersonate
        super().__init__(*args, **kwargs)
        if not hasattr(self, "hooks") or not self.hooks:
            self.hooks = {"response": []}
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.request_count = 0
        self.measurement_method = "curl-http-transfer" if CURL_CFFI_AVAILABLE else "payload-fallback"

    def request(self, method, url, *args, **kwargs):
        resp = super().request(method, url, *args, **kwargs)
        self.request_count += 1

        # curl_cffi exposes libcurl's HTTP transfer counters. They include the
        # HTTP request/response headers and bodies, but not TCP/IP/TLS framing.
        tx = getattr(resp, "request_size", 0) or 0
        rx = getattr(resp, "response_size", 0) or 0
        if not tx and not rx:
            # Keep the app usable without curl_cffi, but never label this as
            # wire-accurate: requests exposes payloads, not proxy socket bytes.
            self.measurement_method = "payload-fallback"
            if hasattr(resp, "request"):
                req_body = getattr(resp.request, "body", b"") or b""
                tx = len(req_body) if isinstance(req_body, (bytes, bytearray)) else len(str(req_body).encode("utf-8"))
            rx = len(getattr(resp, "content", b"") or b"")

        self.tx_bytes += tx
        self.rx_bytes += rx

        hooks = getattr(self, "hooks", {})
        for hook in hooks.get("response", []):
            try:
                hook(resp)
            except Exception:
                pass
        return resp


class EverAIError(Exception):
    """Base exception for EverAI errors."""
    pass


class EverAIQuotaError(EverAIError):
    """Raised when EverAI returns 429 or rate/character quota exceeded."""
    def __init__(self, message: str = "Bạn đã vượt quá giới hạn chuyển đổi (429)."):
        super().__init__(message)


class EverAIConnectionError(EverAIError):
    """Raised when network/proxy connection fails."""
    pass


class EverAICancelled(EverAIError):
    pass


class EverAIClient:
    """
    Direct HTTP Client for EverAI Text-to-Speech API.
    Interacts with https://www.everai.vn/text-to-speech.
    Equipped with TLS JA3/JA4 Fingerprint Impersonation and HTTP/2 support.
    """

    BASE_URL = "https://www.everai.vn"
    TTS_PAGE_URL = "https://www.everai.vn/text-to-speech"
    CREATE_URL = "https://www.everai.vn/text-to-speech/create-request"
    GET_URL = "https://www.everai.vn/text-to-speech/get-request"

    def __init__(
        self,
        proxy: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: int = 30,
        device_profile: Optional[Any] = None,
        download_via_direct_ip: bool = True,
        csrf_token: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        """
        :param proxy: Optional proxy string e.g. "http://user:pass@host:port" or "host:port"
        :param session_id: Optional logged in Django sessionid cookie
        :param timeout: HTTP request timeout in seconds
        :param device_profile: Optional DeviceProfile for device spoofing / fingerprint rotation
        :param download_via_direct_ip: If True, downloads MP3 directly via machine IP (bypassing proxy for speed & reliability)
        :param csrf_token: Optional pre-fetched CSRF token to skip downloading full HTML landing page
        :param log_callback: Optional callback func(msg: str, level: str) for live GUI logging
        """
        self.proxy_str = proxy
        self.session_id = session_id
        self.timeout = timeout
        self.device_profile = device_profile
        self.download_via_direct_ip = download_via_direct_ip
        self.log_callback = log_callback
        self.cancel_event = threading.Event()

        impersonate_target = getattr(self.device_profile, "impersonate", "chrome131") if self.device_profile else "chrome131"
        self.impersonate_target = impersonate_target if CURL_CFFI_AVAILABLE else None
        self.is_tls_impersonated = CURL_CFFI_AVAILABLE

        self.session = ImpersonatedSession(impersonate=impersonate_target)
        self.session.trust_env = False
        if CURL_CFFI_AVAILABLE:
            self.log(f"[CLIENT] 🛡️ Kích hoạt TLS Impersonation sâu: '{impersonate_target}' (HTTP/2 + JA3/JA4 Chrome)", "DEBUG")
        else:
            self.log("[CLIENT] curl_cffi không khả dụng, sử dụng requests tiêu chuẩn", "DEBUG")

        # Dedicated direct session for downloading audio directly via machine IP (bypassing proxy)
        self.direct_session = ImpersonatedSession(impersonate=impersonate_target)
        self.direct_session.trust_env = False
        self.direct_session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        })

        self._configure_session()
        self.csrf_token: Optional[str] = csrf_token
        if self.csrf_token:
            self.session.cookies.set("csrftoken", self.csrf_token, domain="www.everai.vn")

    def get_traffic_stats(self) -> Dict[str, Any]:
        """
        Returns cumulative HTTP transfer counters in bytes. With curl_cffi,
        request_size/response_size include HTTP headers and bodies. They do
        not include TCP/IP/TLS framing or the proxy provider's billing rules.
        """
        proxy_tx = getattr(self.session, "tx_bytes", 0)
        proxy_rx = getattr(self.session, "rx_bytes", 0)
        direct_tx = getattr(self.direct_session, "tx_bytes", 0)
        direct_rx = getattr(self.direct_session, "rx_bytes", 0)
        return {
            "proxy_tx_bytes": proxy_tx,
            "proxy_rx_bytes": proxy_rx,
            "proxy_total_bytes": proxy_tx + proxy_rx,
            "direct_tx_bytes": direct_tx,
            "direct_rx_bytes": direct_rx,
            "direct_total_bytes": direct_tx + direct_rx,
            "total_bytes": proxy_tx + proxy_rx + direct_tx + direct_rx,
            "proxy_request_count": getattr(self.session, "request_count", 0),
            "direct_request_count": getattr(self.direct_session, "request_count", 0),
            "measurement": self._measurement_method(),
        }

    def _measurement_method(self) -> str:
        methods = {getattr(self.session, "measurement_method", "unknown"),
                   getattr(self.direct_session, "measurement_method", "unknown")}
        return "curl-http-transfer" if methods == {"curl-http-transfer"} else "payload-fallback"

    def log(self, message: str, level: str = "INFO") -> None:
        """Emits a log message to Python logging and the optional UI callback."""
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

    def _configure_session(self) -> None:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": self.BASE_URL,
            "Referer": self.TTS_PAGE_URL,
        }

        if self.device_profile:
            headers.update(self.device_profile.to_headers())
            self.log(f"[CLIENT] 📱 Thiết bị giả lập: {self.device_profile.name} (TLS: {getattr(self.device_profile, 'impersonate', 'chrome131')})", "DEBUG")
        else:
            headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            })

        self.session.headers.update(headers)

        if self.proxy_str:
            try:
                from .utils import parse_proxy
            except ImportError:
                from utils import parse_proxy
            parsed = parse_proxy(self.proxy_str)
            if parsed:
                proxy_url = parsed.to_url()
                self.session.proxies = {
                    "http": proxy_url,
                    "https": proxy_url,
                }
                self.log(f"[CLIENT] Sử dụng proxy: {parsed}", "DEBUG")
            else:
                clean_proxy = self.proxy_str.strip()
                if not clean_proxy.startswith("http://") and not clean_proxy.startswith("https://"):
                    clean_proxy = f"http://{clean_proxy}"
                self.session.proxies = {
                    "http": clean_proxy,
                    "https": clean_proxy,
                }
                self.log(f"[CLIENT] Sử dụng proxy: {clean_proxy}", "DEBUG")

        if self.session_id:
            self.session.cookies.set("sessionid", self.session_id, domain="www.everai.vn")
            self.log("[CLIENT] Đã nạp Cookie sessionid cho tài khoản", "DEBUG")

    def test_connection(self) -> Dict[str, Any]:
        """Tests connectivity and checks the public IP address seen by the server."""
        self.log("[CLIENT] Đang kiểm tra kết nối mạng / Proxy...", "INFO")
        try:
            r = self.session.get("https://api.ipify.org?format=json", timeout=self.timeout)
            if r.status_code == 200:
                ip_info = r.json()
                public_ip = ip_info.get("ip", "unknown")
                self.log(f"[CLIENT] Kết nối thành công! IP Egress phát hiện: {public_ip}", "INFO")
                return {"success": True, "ip": public_ip}
            else:
                self.log(f"[CLIENT] Kiểm tra IP trả về mã lỗi: {r.status_code}", "WARNING")
                return {"success": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            self.log(f"[CLIENT] Lỗi kiểm tra kết nối: {e}", "ERROR")
            return {"success": False, "error": str(e)}

    def fetch_csrf_token(self) -> str:
        """Fetches the TTS landing page to obtain a valid CSRF token."""
        self.log(f"[CLIENT] Đang gửi GET {self.TTS_PAGE_URL} để lấy CSRF token...", "DEBUG")
        last_exc = None
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            if self.cancel_event.is_set():
                raise EverAICancelled("Đã dừng tác vụ.")
            start_t = time.time()
            try:
                # SRT fast mode only: Django issues the same CSRF cookie on HEAD,
                # avoiding the large TTS HTML body. Keep the same proxy/profile.
                head = getattr(self.session, 'head', None)
                if getattr(self, 'prefer_csrf_head', False) and callable(head):
                    try:
                        response = head(self.TTS_PAGE_URL, timeout=self.timeout, allow_redirects=True)
                        token = (self.session.cookies.get('csrftoken') or
                                 (getattr(response, 'cookies', None) or {}).get('csrftoken'))
                        if response.status_code == 200 and isinstance(token, str) and token:
                            self.csrf_token = token
                            self.session.cookies.set('csrftoken', token, domain='www.everai.vn')
                            self.log(f'[CLIENT] Lấy CSRF token thành công ({time.time()-start_t:.2f}s)', 'DEBUG')
                            self.log('[CLIENT] CSRF bằng HEAD, không tải HTML.', 'DEBUG')
                            return token
                    except REQUEST_EXCEPTIONS:
                        self.log('[CLIENT] HEAD chưa có CSRF; dùng GET cùng tuyến.', 'DEBUG')
                resp = self.session.get(self.TTS_PAGE_URL, timeout=self.timeout)
                elapsed = time.time() - start_t
                if resp.status_code != 200:
                    raise EverAIConnectionError(
                        f"Không thể tải dịch vụ Voice Pro (HTTP {resp.status_code}) trong {elapsed:.2f}s"
                    )

                token = self.session.cookies.get("csrftoken") or getattr(resp, "cookies", {}).get("csrftoken")
                if not token:
                    import re
                    m = re.search(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']', resp.text)
                    if m:
                        token = m.group(1)

                if not token:
                    raise EverAIError("Không tìm thấy token phiên Voice Pro.")

                self.csrf_token = token
                self.session.cookies.set("csrftoken", token, domain="www.everai.vn")
                self.log(f"[CLIENT] Lấy CSRF token thành công ({elapsed:.2f}s)", "DEBUG")
                return token
            except REQUEST_EXCEPTIONS as e:
                last_exc = e
                self.log(f"[CLIENT] Lần {attempt}/{max_attempts}: Lỗi kết nối khi lấy CSRF: {e}", "WARNING" if attempt < max_attempts else "ERROR")
                if attempt < max_attempts:
                    if self.cancel_event.wait(1.5):
                        raise EverAICancelled("Đã dừng tác vụ.")
                    continue
        raise EverAIConnectionError(f"Lỗi kết nối khi lấy CSRF token: {last_exc}") from last_exc

    def create_request(
        self,
        input_text: str,
        speaker_id: str = "vi_male_leduc_mb",
        model_id: str = "everai-v1.6",
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: int = 100,
        bitrate: str = "128 kbps",
        audio_type: str = "mp3",
    ) -> str:
        """
        Submits a TTS generation request to EverAI.
        Returns request_id (UUID string).
        """
        if not input_text or not input_text.strip():
            raise EverAIError("Văn bản đầu vào không được để trống.")

        char_count = len(input_text)
        if char_count > 500:
            raise EverAIError(
                f"Độ dài văn bản ({char_count} ký tự) vượt quá giới hạn 500 ký tự của EverAI. "
                "Cần chia nhỏ văn bản trước khi gửi."
            )

        if not self.csrf_token:
            self.fetch_csrf_token()

        post_headers = {
            "Content-Type": "application/json",
            "X-CSRFToken": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.TTS_PAGE_URL,
            "Origin": self.BASE_URL,
        }

        # Format speed & pitch as strings e.g. "1.0"
        speed_str = f"{float(speed):.1f}"
        pitch_str = f"{float(pitch):.1f}"
        volume_str = str(int(volume))

        # Format bitrate e.g. "128 kbps"
        if not bitrate.endswith("kbps"):
            bitrate_str = f"{bitrate} kbps"
        else:
            bitrate_str = bitrate

        payload = {
            "input_text": input_text.strip(),
            "model_id": model_id,
            "audio_type": audio_type,
            "speed": speed_str,
            "pitch": pitch_str,
            "volume": volume_str,
            "bitrate": bitrate_str,
            "speaker_id": speaker_id,
        }

        self.log(
            f"[CLIENT] Gửi POST create-request | Model: {model_id} | Speaker: {speaker_id} | Ký tự: {char_count} | Tốc độ: {speed_str}x",
            "INFO",
        )

        try:
            resp = self.session.post(
                self.CREATE_URL,
                headers=post_headers,
                json=payload,
                timeout=self.timeout,
            )
        except REQUEST_EXCEPTIONS as e:
            self.log(f"[CLIENT] Lỗi kết nối khi gọi create-request: {e}", "ERROR")
            raise EverAIConnectionError(f"Lỗi mạng khi tạo yêu cầu: {e}") from e

        if resp.status_code != 200:
            self.log(f"[CLIENT] Server trả về mã HTTP {resp.status_code}", "ERROR")
            if resp.status_code == 429:
                raise EverAIQuotaError("Bạn đã vượt quá giới hạn chuyển đổi (HTTP 429).")
            raise EverAIError(f"EverAI trả về lỗi HTTP {resp.status_code}")

        try:
            data = resp.json()
        except Exception as e:
            self.log("[CLIENT] Phản hồi không phải JSON", "ERROR")
            raise EverAIError(f"Phản hồi không hợp lệ từ server: {e}")

        # Check for Quota Exceeded (EverAI returns status 0 with error_code 429)
        status = data.get("status")
        err_code = data.get("error_code")
        err_msg = data.get("error_message", "")

        if status == 0 or err_code == 429:
            self.log(f"[CLIENT] Phản hồi lỗi từ server: code={err_code}", "WARNING")
            if err_code == 429 or "vượt quá giới hạn" in err_msg.lower():
                raise EverAIQuotaError(f"EverAI Quota: {err_msg} (Mã lỗi: {err_code})")
            raise EverAIError(f"Lỗi tạo audio EverAI: {err_msg} (code {err_code})")

        result = data.get("result", {})
        request_id = result.get("request_id")
        if not request_id:
            raise EverAIError("Không nhận được request_id trong phản hồi dịch vụ.")

        self.log(f"[CLIENT] Đã tạo thành công request_id: {request_id}", "INFO")
        return request_id

    def poll_for_audio_url(
        self,
        request_id: str,
        max_attempts: int = 35,
        interval: float = 1.0,
    ) -> str:
        """
        Polls get-request until status becomes 'hoàn thành'.
        Returns direct Cloudflare R2 presigned audio URL.
        """
        poll_headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.TTS_PAGE_URL,
        }
        url = f"{self.GET_URL}?request_id={request_id}"

        self.log(f"[CLIENT] Bắt đầu theo dõi tiến độ request_id: {request_id}...", "DEBUG")

        for attempt in range(1, max_attempts + 1):
            if self.cancel_event.wait(interval):
                raise EverAICancelled("Đã dừng tác vụ.")
            try:
                resp = self.session.get(url, headers=poll_headers, timeout=self.timeout)
            except REQUEST_EXCEPTIONS as e:
                self.log(f"[CLIENT] Lần {attempt}: Lỗi mạng khi kiểm tra tiến độ ({e})", "WARNING")
                continue

            if resp.status_code != 200:
                self.log(f"[CLIENT] Lần {attempt}: get-request HTTP {resp.status_code}", "WARNING")
                continue

            try:
                data = resp.json()
            except Exception:
                continue

            result = data.get("result", {})
            status = result.get("status")
            progress = result.get("progress", 0)

            self.log(f"[CLIENT] Lần {attempt}/{max_attempts}: trạng thái='{status}', tiến trình={progress}%", "DEBUG")

            if status == "hoàn thành":
                audio_url = result.get("url")
                if not audio_url:
                    raise EverAIError("Hoàn thành nhưng không có audio URL trong phản hồi.")
                self.log(f"[CLIENT] Xử lý âm thanh hoàn tất 100%! Sẵn sàng tải về.", "INFO")
                return audio_url
            elif status == "error":
                raise EverAIError(f"Xử lý âm thanh thất bại ở server (status: error).")

        raise EverAIError(f"Hết thời gian chờ (Timeout sau {max_attempts * interval:.1f}s) cho request {request_id}")

    def download_audio(self, audio_url: str, use_direct_ip: Optional[bool] = None) -> bytes:
        """
        Downloads the audio binary from the presigned Cloudflare R2 audio URL.
        By default uses direct machine IP (no proxy) for maximum download speed,
        saving proxy bandwidth and avoiding proxy timeouts.
        """
        if use_direct_ip is None:
            use_direct_ip = self.download_via_direct_ip

        channel_desc = "IP máy trực tiếp (Tốc độ cao, không qua Proxy)" if use_direct_ip else "Proxy"
        self.log(f"[CLIENT] ⚡ Đang tải file âm thanh MP3 từ R2 storage qua {channel_desc}...", "INFO")

        download_session = self.direct_session if use_direct_ip else self.session
        try:
            resp = download_session.get(audio_url, timeout=max(self.timeout, 30))
            if resp.status_code != 200:
                raise EverAIError(f"Không thể tải file âm thanh (HTTP {resp.status_code})")

            content = resp.content
            if not content:
                raise EverAIError("Dữ liệu âm thanh tải về rỗng (0 bytes).")
            if not (content.startswith(b"ID3") or (len(content) >= 2 and content[0] == 255 and content[1] & 224 == 224)):
                raise EverAIError("Dịch vụ không trả về dữ liệu MP3 hợp lệ.")

            self.log(f"[CLIENT] ✓ Tải thành công {len(content):,} bytes âm thanh MP3 ({channel_desc})", "INFO")
            return content
        except REQUEST_EXCEPTIONS as e:
            self.log(f"[CLIENT] Lỗi khi tải file âm thanh: {e}", "ERROR")
            raise EverAIConnectionError(f"Lỗi khi tải file âm thanh: {e}") from e

    def synthesize_one_chunk(
        self,
        text: str,
        speaker_id: str = "vi_male_leduc_mb",
        model_id: str = "everai-v1.6",
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: int = 100,
        bitrate: str = "128 kbps",
        audio_type: str = "mp3",
    ) -> bytes:
        """All-in-one helper for a single chunk under 500 chars."""
        if self.cancel_event.is_set():
            raise EverAICancelled("Đã dừng tác vụ.")
        req_id = self.create_request(
            input_text=text,
            speaker_id=speaker_id,
            model_id=model_id,
            speed=speed,
            pitch=pitch,
            volume=volume,
            bitrate=bitrate,
            audio_type=audio_type,
        )
        audio_url = self.poll_for_audio_url(req_id)
        if self.cancel_event.is_set():
            raise EverAICancelled("Đã dừng tác vụ.")
        return self.download_audio(audio_url, use_direct_ip=self.download_via_direct_ip)

    def close(self):
        self.session.close()
        self.direct_session.close()
