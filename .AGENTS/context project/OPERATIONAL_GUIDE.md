# Vận hành dữ liệu, proxy và chẩn đoán

Thao tác UI xem [USER_GUIDE.md](USER_GUIDE.md); release/hash xem [CURRENT_STATE.md](CURRENT_STATE.md).

## Đường dẫn và lưu trữ

Windows mặc định `%LOCALAPPDATA%/EverAIStudio/`; có `portable.flag` cạnh EXE (hoặc module khi chạy source) thì dùng `EverAIStudio-data/` cạnh đó. Không lưu dữ liệu ở `_MEIPASS`. Portable không ghi được phải báo/hỏi nơi khác, không âm thầm đổi ví. `writable_directory` thử tạo/ghi/flush/fsync 4 KB để phát hiện ổ đầy; output audio cũng cần dung lượng.

| Dữ liệu | Vị trí dưới data root |
| --- | --- |
| Cấu hình | settings.json; có ElevenLabs API key local, không phải vault mã hóa |
| Nội dung đang soạn | draft.json; có text/source/filename |
| Kết quả gần đây | recent.json; tối đa 10, bỏ lịch sử không xóa MP3 |
| Proxy thực dùng | proxies.json; có thể chứa credentials, không đưa vào log/context/build |
| Bộ đếm proxy | proxy_traffic.json |
| Khóa cập nhật JSON | store-lock.sqlite3; không phải DB ví |
| Phục hồi | jobs/<id>/manifest.json và chunks; preview jobs dưới samples/jobs |
| Preview cache | samples/; tách bộ mẫu nguồn |
| Ví/OS job lock | licensing/wallet.sqlite3 và licensing/locks/ |
| Log xoay vòng | logs/studio.log |

Mẫu nguồn ở assets/voice_samples trong source hoặc resource gói, có manifest/hash; không bị dọn theo cache preview. Cookie CapCut ở TEMP/EverAIStudio-TTS/<hash tuyến>, ngoài data root, không dùng chung upstream app. Android lưu riêng private files của app qua NativeBridge, output tạm trong audio trước SAF/share.

Source lần đầu có thể migration data/everai_proxies.json nếu chưa có proxies.json; không sửa/xóa file gốc. Bản khách tạo catalog rỗng, `use_proxy=True`; proxy builtin mặc định không ghi vào `proxies.json`. JSON hỏng được giữ bản `.corrupt-<timestamp>` trước dùng mặc định/cho lưu mới; lỗi đọc/backup không được che bằng ghi catalog trống.

## Proxy và cấu hình

Saydi Ngọc Huyền (`saydi`) cũng luôn kết nối trực tiếp, không dùng proxy/env
proxy. Bearer token chỉ RAM và mỗi chunk có session riêng; cần nhập lại token
sau khi mở app. Không có bước tự lấy token hoặc giải Turnstile. Xem chi tiết
auth/chunk/retry tại TTS_PROVIDER_MODULE.md.

Ngoại lệ CapCut từ sửa 25/10/2026: CapCut luôn kết nối trực tiếp cho tạo task,
poll và tải MP3; bỏ qua cả proxy catalog và proxy môi trường. Chỉ Edge/ElevenLabs
còn dùng adapter HTTP/HTTPS. Xem TTS_PROVIDER_MODULE.md; các mô tả chung cũ về
proxy của cả ba provider không áp dụng cho CapCut sau bản sửa này.

Thêm tĩnh/xoay → Lưu proxy → Kích hoạt → Lưu cài đặt. Proxy luôn bật; không còn checkbox “Sử dụng proxy đã kích hoạt”. Tạo bài cũng kiểm tra/lưu settings đang nhập. `get_active_proxy` ưu tiên mục `is_active` có địa chỉ/scheme/port hợp lệ, loại placeholder `proxy.example.invalid`; nếu không có thì trả proxy builtin mặc định ẩn. Proxy mới/import chưa tự active. Engine EverAI giữ proxy builtin hoặc mục custom active; retry không tự nhảy sang mục catalog khác.

ProxyCatalog có health check, link rotate, lịch tự xoay. Giữ next_rotate_at khi lưu mà không đổi tuyến/chu kỳ; không để form cũ ghi đè lịch/active do runtime đổi. Quota EverAI tôn trọng rotate_on_429; lỗi mạng có nhánh force_rotate. API không tự direct fallback khi yêu cầu proxy mà chưa có tuyến. Scheduler treo trong job GUI; kiểm tra/xoay mạng chạy nền, không chạm Tk từ worker.

EverAI sessionid chỉ trong RAM. Tải MP3 trực tiếp mặc định bật, dù API qua proxy; đây là tùy chọn đường tải audio riêng, không tắt proxy API. Primary/direct trust_env=False. Provider mới nhận HTTP/HTTPS qua adapter riêng, không dùng direct-download/randomize-device như EverAI; proxy builtin SOCKS chỉ dành cho client EverAI, nên provider mới cần custom HTTP/HTTPS active; chưa nghiệm thu proxy live cho ba provider.

### Cơ chế Anti-Detect Xoay IP & Fingerprint từng phân đoạn (25/10/2026)

Hệ thống hỗ trợ cơ chế ngụy trang cao cấp theo từng phân đoạn (Chunk) khi tùy chọn **"Thay đổi hồ sơ thiết bị"** (`randomize_device`) được bật:
- **Fingerprint động:** Mỗi chunk khi tạo yêu cầu đều được cấp một `DeviceProfile` ngẫu nhiên mới (User-Agent, sec-ch-ua, sec-ch-ua-platform, Client Hints, Chrome TLS Impersonation qua `curl_cffi`).
- **CSRF Token độc lập:** Mỗi chunk tạo một phiên HTTP độc lập và tự fetch `csrftoken` riêng qua IP của chunk đó; không dùng chung token cũ giữa các chunk để đảm bảo tính toàn vẹn danh tính.
- **Hỗ trợ đa dạng loại Proxy:**
  1. *Proxy dân cư xoay theo request (như NodeMaven gateway `31.59.160.7:8080:np_...`):* Mỗi kết nối HTTP tự động nhận một IP egress khác nhau từ các nhà mạng (Viettel, VNPT, FPT...).
  2. *Proxy xoay bằng Link API (`rotate_url`):* Tuyến custom active giữ nguyên; engine gọi link khi retry phù hợp cấu hình quota/lỗi mạng. Không bảo đảm đổi IP trước mỗi chunk.
  3. *Catalog proxy tĩnh:* Source sửa logic 25/10 chỉ dùng mục custom được kích hoạt hoặc builtin fallback, không dùng các mục mới/import chưa active. Riêng danh sách `proxies=` truyền explicit vào engine Python vẫn hỗ trợ chọn vòng theo chunk; không đồng nhất danh sách đó với catalog GUI.
- **Gia cố đường truyền:** Timeout HTTP mặc định 30 giây kèm cơ chế retry 2 lần với backoff 1.5s trong `fetch_csrf_token()` để chống giật lag mạng.

### Đóng gói bản sao White-Label Voice Pro Studio

Dự án cung cấp script đóng gói độc lập `build_voicepro.py` để tạo bản phát hành thương hiệu riêng **Voice Pro Studio**:
```powershell
python build_voicepro.py --dist dist/release-YYYYMMDD-voicepro-new
```
- Tự động thay thế toàn bộ nhận diện "Ever AI" sang "Voice Pro" trên các file staging tạm thời.
- Tạo file thực thi `Voice_Pro.exe`; builder hiện đặt ZIP theo tên thư mục `--dist` (ví dụ `dist/release-20261025-voicepro-desktop-fixes-r2.zip`), không ghi đè `Voice_Pro_Studio.zip` cũ.
- Giữ nguyên 100% mã nguồn gốc `src/everai/` và không làm ảnh hưởng đến bộ test chuẩn.

Bản desktop-fixes-r2 chỉ đổi hằng nhãn UI (`PRIMARY_PROVIDER_LABEL`, `PRIMARY_MODEL_PREFIX`) khi stage GUI. ID provider `everai` và model `everai-v1.5/v1.6` trong settings/job/API giữ nguyên, tránh lỗi khôi phục và tránh đổi fingerprint chỉ vì thương hiệu.

## Lưu lượng: ý nghĩa và giới hạn

EverAI ImpersonatedSession đếm request/response sau khi có response; engine cộng mỗi attempt, GUI `_finish` ghi tổng vào StudioStore theo proxy. Bảng/CSV lưu byte int, nhãn KB chia 1024 (đơn vị nhị phân), có reset xác nhận và dữ liệu bền qua lần mở.

- curl-http-transfer: counter HTTP libcurl; payload-fallback khi thiếu counter. Không đo đầy đủ TLS/TCP/HTTP2 wire overhead hay cước provider.
- Response lỗi/retry nhận response được cộng; lỗi trước response chưa đo đủ.
- Health check/rotate URL không gom vào bảng. Crash trước `_finish` có thể mất thống kê tác vụ.
- Primary stats vẫn mang tên proxy khi chạy direct; không coi tổng này là proxy thực dùng.
- get_current_proxy và `_proxy_context` đọc active ở hai thời điểm; concurrency/rotation có thể quy gán lệch. Chưa phải bộ đo quyết toán.
- Provider CapCut/Edge/ElevenLabs chưa tích hợp counter; số 0 không phải bằng chứng không có mạng.

Đối chiếu dashboard nhà cung cấp nếu cần quyết toán; không mô tả bộ đếm app chính xác tuyệt đối hóa đơn.

## Chẩn đoán và phục hồi

Log hoạt động hiện trên UI; nhật ký kỹ thuật cần mật khẩu đã lưu hash trước khi tạo cửa sổ AdminLogDialog. Buffer giới hạn/log file xoay vòng, redact token/cookie/credential/URL nhạy cảm; không log toàn văn đầu vào. Khóa GUI không thay quyền bảo vệ file hệ điều hành. Hỗ trợ chỉ sao chép phiên bản/OS/giai đoạn/mã lỗi, không export settings chứa key.

| Triệu chứng | Hướng xử lý |
| --- | --- |
| Thiếu `_internal`/runtime | Giải nén đầy đủ ZIP đúng bản, giữ cây file; không copy riêng EXE |
| Không ghi được/ổ đầy | Chọn nơi ghi còn dung lượng; giữ data root/ví cũ, không tự reset hoặc di chuyển |
| Thiếu/hỏng mẫu EverAI | Khôi phục asset đúng manifest; không fallback gọi provider |
| Proxy custom sai | Sửa/kích hoạt/lưu; nếu không có custom hợp lệ app vẫn dùng proxy builtin, không mở direct để che lỗi |
| Job dở | Giữ text/config, chọn Tiếp tục; chỉ tạo chunk thiếu/hỏng, cho đổi nơi xuất |
| FFmpeg lỗi | Sửa FFmpeg/đường ghi rồi resume ghép, không tạo bài mới nếu còn chunk paid |
| Ví/key lỗi | Giữ dữ liệu, xem ADMIN_KEY_GUIDE; không tự xóa DB hoặc sinh khóa mới |
| Provider mới lỗi | Kiểm tra Node/Edge/FFmpeg/key/model/voice/quyền tài khoản; phân biệt authentication/payment/rate_limit |
| Lỗi kết nối khi lấy CSRF token | Đã khắc phục ở bản 25/10: tăng timeout lên 30s, thêm retry 2 lần. Kiểm tra proxy có mạng, ping proxy trong tab Quản lý proxy |
| Lỗi link xoay IP (status 102) | API xoay hết hạn key hoặc sai cú pháp link. Kiểm tra lại key với nhà cung cấp proxy hoặc tắt auto-rotate |

Benchmark EverAI 3 luồng ngày 20/10 ghi nhận 5 chunk/1.432 ký tự trong 24,685 giây qua một proxy; chỉ là mẫu đo lịch sử, không SLA hay giới hạn provider. Nếu cần truy nguyên có `logs/benchmark-3-threads-20261020-172721/`, không nạp credential/log thô vào context.
