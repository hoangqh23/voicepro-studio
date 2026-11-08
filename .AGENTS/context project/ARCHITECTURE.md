# Kiến trúc và luồng thực thi hiện tại

## Project SRT desktop, 26/10

`srt_studio/` quản lý project riêng cho mọi provider. Qt `input_mode=srt` →
SrtPanel (table model/view + cue drafts) → SrtRunner.estimate trong TaskThread
→ xác nhận hạn mức → runner.process. Project lock bao quanh reload, configure
và tác vụ; JSON atomic. Cue UUID ổn định, generation/job bất biến khi text/config
thay đổi; timestamp và fit policy thuộc project, không thuộc job con.

Cue text thuần → EverAITTSEngine hoặc MultiProvider(input_mode='text') → một
hay nhiều chunk → raw.mp3 giữ ngoài job/ sau khi core dọn chunk. License vẫn
reserve/commit theo child job. Bản fast: mọi provider tối đa K cue × 1 worker,
không K×K. EverAI dùng RouteCoordinator chung: request lease song song,
drain/chặn request khi rotate, epoch gộp lỗi đồng thời; không thay retry core.
Child EverAI SRT bật CSRF HEAD cùng session/proxy/profile, fallback GET nếu
không có token. Text/TXT không bật. Hủy gọi mọi child rồi đợi hoàn tất.
Parent checkpoint được hạn chế tần suất; child manifest/raw-metadata và fit
cache metadata bền vững độc lập, có đối soát sau crash. Child không lẫn vào
StudioStore.unfinished_jobs chỉ quét data/jobs, còn SRT ở data/srt-projects.

Raw giữ hash ngay sau remux; frames có thể null tới fit, tránh decode lặp.
Decode PCM mono48k16 khi fit, đo frame thực. Fit v2 cắt silence biên bảo thủ,
giới hạn khoảng nghỉ nội bộ, mượn tối đa 250 ms khe trước/sau rồi atempo trong
ngưỡng 1,35× hoặc 4× và đo lại. Nếu vẫn dài, không ném FitError: giữ đủ PCM,
đặt nối tiếp sau cue trước, đẩy cursor và bắt kịp bằng các khe sau. Không cắt
lời/không chồng speech; output=max(mốc SRT cuối, speech cuối). Cue ngắn không
đệm trong fit cache; zeros chỉ được ghi khi ghép streaming, không dùng stems/
amix. WAV là chuẩn timeline; MP3 encode một lần và decode kiểm sai lệch <=50
ms. Fit cache gắn raw hash/dynamic window/policy/version; placement có frame
riêng. Fit/export không cần token/license còn hạn nếu raw hợp lệ; toàn bộ chunk
paid hợp lệ của job chưa ghép cũng được assemble theo core, không gọi TTS mới.

API/TXT/Tk/Android vẫn giữ luồng cũ. Tài liệu thuật toán và test matrix:
[SRT_TIMELINE_PLAN.md](SRT_TIMELINE_PLAN.md). Các mô tả `tts/srt.py` bên dưới
là đường legacy, không phải thuật toán placement của project SRT mới.

## Desktop PyQt6, 26/10

Entry hiện hành gọi `qt_gui.launch_gui`, không dùng Tk. `StudioWindow` gồm
năm trang sidebar và transport cố định, theme `qt_theme.py`, preference riêng
`qt-ui.json`. Worker QThread chỉ chạy engine/I/O; signal finished sau run,
queue/QTimer cập nhật widget main thread. Đóng app chờ hủy/worker/network.
Store/license/jobs/proxy/player dùng lại core, không import src/core/src/utils.
Chi tiết và test ở [QT_UI_DESIGN.md](QT_UI_DESIGN.md). Các mục Tk bên dưới
mô tả legacy/baseline; tên method Qt không có prefix underscore giống Tk.

Saydi Ngọc Huyền bổ sung 25/10: `tts/saydi.py` cung cấp POST demo MP3 theo
tài liệu người dùng. `TTSService` có token Saydi riêng trong RAM, không Node,
không proxy; GUI thêm provider/ô token và giữ voice ID `saydi|vi-hn-ngoc-huyen`.
`MultiProviderTTSEngine.process` xác định provider trước fingerprint/chia chunk,
chốt trần 350 ký tự cho Saydi; GUI `speech_chunks` dùng cùng trần cho text/SRT.
Job/license/queue/resume/preview/SRT/publish vẫn đi qua lõi Studio. Chỉ Saydi và
CapCut là các nhánh direct Python; Edge/ElevenLabs giữ Node adapter.
Xem TTS_PROVIDER_MODULE.md và CURRENT_STATE.md cho auth/bằng chứng/artifact.

Đối chiếu source/CodeGraph 23/10/2026. Artifact và phần chưa nghiệm thu ở [CURRENT_STATE.md](CURRENT_STATE.md).

## Bản đồ thành phần

Desktop-fixes-r2 (25/10): `PRIMARY_PROVIDER_LABEL`/`PRIMARY_MODEL_PREFIX` chỉ đổi nhãn, `_provider_id` và `_snapshot` giữ ID canonical; builder dùng `brand_gui_source` thay đổi nhãn thay vì sửa ID catalog. `run_audio_command` trong utils theo dõi subprocess FFmpeg, hủy/deadline rồi kill/reap trước trả lỗi; concat, timeline và normalization dùng chung. `split_srt_chunks` chia cue dùng chung GUI/engine. Recovery lọc queue done/skipped, cập nhật banner khi đổi queue và có xác nhận trước khi nạp lại text/config từ manifest. `_finish` xử lý thông báo lỗi provider được giữ nguyên theo yêu cầu loại trừ.

Bản sửa source 25/10 bổ sung `api_server.py`: chỉ chia sẻ cấu hình/catalog, mỗi request tạo engine riêng và thư mục tạm riêng để cô lập hủy/CSRF/counter, dọn output/chunk/manifest cả khi lỗi. Catalog engine chỉ dùng active/builtin, không chọn cả các mục chưa active. Plugin WordPress 1.0.1 chỉ cho khách đọc cache; tạo mới/refresh cần admin hoặc PHP backend tin cậy, cache có `flock` và atomic rename. Xem [WORDPRESS_INTEGRATION_GUIDE.md](WORDPRESS_INTEGRATION_GUIDE.md).

| Module | Trách nhiệm |
| --- | --- |
| `run.py`, `__main__.py` | Entry source standalone/package; frozen launcher được builder tạo riêng |
| `gui.py` / EverAIGuiApp | Snapshot, provider/filter, draft, worker/queue, kết quả, preview, phục hồi |
| `settings_dialog.py` | Một bộ biến Lưu/Hủy/khóa, proxy tab và preferences Toplevel |
| `ui_theme.py`, `audio_player.py` | Card/StudioButton, CustomTkinter; player Windows MCI |
| `engine.py` / EverAITTSEngine | Chunk/job/fingerprint, license, executor, retry EverAI, ghép và publish |
| `client.py` | HTTP EverAI, CSRF, polling, hai session, lỗi/counter |
| `proxy_manager.py` | ProxyItem/ProxyCatalog, CRUD/active, health, rotate URL, scheduler |
| `device_profiles.py`, `voices.py` | Profile header/impersonation và 49 ID EverAI |
| `utils.py` | StudioStore, đường dẫn, JSON atomic/lock, normalize/chunk/SRT cũ, FFmpeg/publish/redaction |
| `license_manager.py`, `license_dialog.py` | Ed25519 verify, mã máy, ví SQLite, reservation/ledger/recovery, panel key |
| `admin_key/admin_tool.py` | Issuer GUI/CLI và backup/restore, chỉ máy quản trị |
| `tts/` | Renderer ba provider, catalog/languages, SRT timeline, worker Edge; xem chuyên đề |
| `android_bridge.py`, `android_transport.py`, `android_crypto.py`, `android/` | Adapter/native UI/transport/crypto/audio Android |
| `build_portable.py`, `prepare_ffmpeg.py`, `prepare_voice_samples.py`, `tools/` | Đóng gói, asset/provenance, trích TTS/cập nhật catalog |

`ever-module/ever-module/` là source khác đang nằm trong cây; GUI Studio hiện hành không khởi chạy nó. `audit_stress_test.py` và `benchmark_3_threads.py` là công cụ kiểm tra, có thể gọi mạng; không tự chạy khi tiếp quản. Cache/build-temp/dist/log/output/vendor không đồng nghĩa module runtime được import.

## Giao diện và trạng thái

Ba tab: Tạo giọng nói (text trái, chọn provider/giọng/thông số phải, cột phải cuộn), Quản lý proxy, Lịch sử (10 kết quả + hoạt động). Thanh Tạo/Dừng/số luồng/tiến độ/phục hồi/player cố định. Cài đặt góc phải mở Toplevel dùng lại; đóng chỉ ẩn và giữ bản đang sửa, Hủy phục hồi cấu hình đã lưu.

SettingsDialog với `embedded=True, preferences_parent=...` dùng cùng biến cho proxy và preferences; workers chung với app, 1–8/default 3. Tạo/Lưu áp dụng và ghi nhớ; busy khóa form/control. Combobox giọng readonly, popup tối đa 8 dòng, có cuộn. Nút Theo tùy chỉnh cũ đã bỏ; method `_preview_configured` vẫn dùng cho provider mới và tương thích.

Tk main thread đọc queue trong `_poll`; worker không chạm widget. `_run` chụp engine, chạy thread `daemon=False`, treo scheduler proxy trong tác vụ; `_finish` xử lý kết quả/lỗi, lịch sử/lưu lượng/player và mở khóa. Nút Thử lại chọn đúng callback preview hoặc bài chính.

## Tạo bài, dữ liệu và license

1. `_start_generation` lấy text/config từ `_snapshot`, kiểm tra FFmpeg/thư mục ghi. `_new_engine` lưu settings đang nhập, chặn network busy/settings busy, rồi lấy proxy active explicit hoặc proxy builtin mặc định ẩn; chọn EverAITTSEngine hay MultiProviderTTSEngine theo provider. API key không thuộc config job.
2. `process` chuẩn hóa NFC/newline rồi chia tối đa **450** ký tự mặc định (không phải luôn nhỏ hơn 450); constructor chặn max_chunk_chars trên 480, client EverAI chặn trên 500. SRT provider mới chia riêng từng cue.
3. Identity gồm **text đầu vào**, config (speaker/model/speed/pitch/volume/bitrate/audio_type), `chunk_version=2`, max_chunk_chars. SHA256 JSON là fingerprint; đổi text/config phải tạo job mới. Concurrency/proxy/session/API key/output path không nằm trong fingerprint; có thể đổi nơi xuất hoặc số luồng khi resume hợp lệ.
4. Job có manifest ở `jobs/<id>/manifest.json`, text/config/completed/hash/status/output/result. Đọc chunk còn file/hash hợp lệ; file mất/hỏng bỏ khỏi results. Manifest completed chỉ trả file result nếu còn tồn tại; result đã chuyển/mất yêu cầu bài mới.
5. `LicenseManager.job` lấy OS lock và reserve toàn bộ phần chưa trả phí trước executor. Job ID là SHA256 đường dẫn job root; ledger và chunk path/hash bổ sung kết quả nếu crash trước cập nhật manifest. Xem [ADMIN_KEY_GUIDE.md](ADMIN_KEY_GUIDE.md).
6. ThreadPoolExecutor chỉ submit phần thiếu, tối đa min(concurrency, số chunk). Worker lưu file chunk an toàn; dưới khóa, commit phí → gán results → ghi manifest → phát tiến độ. Kết quả lưu theo index, không theo thứ tự hoàn tất.
7. Tiến độ bắt đầu từ chunk đã có; tạo chunk chiếm 0–90%, ghép 95%, đạt 100% sau publish/manifest hoàn tất. Worker lỗi đầu tiên hủy phần chờ; thoát executor vẫn chờ mọi worker kết thúc.
8. `_assemble_audio` mặc định FFmpeg concat/remux kể cả một chunk. Với job_dir, ghép file tạm cạnh đích rồi `publish_audio` tránh ghi đè. Lỗi ghi/FFmpeg giữ chunk/status interrupted để resume. Thành công dọn chunk khi có job_dir; manifest/result vẫn còn.

## Dừng và phục hồi

### Hàng đợi TXT desktop (source 24/10)

GUI giữ txt_queue.json dưới data root: items (id/name/text/state/job/result) và config âm thanh đã chốt; không lưu API key trong queue. Nạp TXT mở bảng duyệt, chỉ xác nhận mới thêm bản chụp text. _queue_start chốt config khi không còn job dở; _queue_next tạo engine theo provider trong config rồi dùng _run hiện có. _poll chờ thread kết thúc và _finish lưu kết quả trước khi bắt đầu file kế tiếp. Mỗi file có job UUID riêng trong jobs/, dùng cùng reservation/ledger/fingerprint/publish. Hàng đợi dừng tại lỗi; người dùng thử lại hoặc bỏ qua. Khởi động đổi running thành trạng thái dở, phục hồi danh sách và chờ thao tác. Khi ghi queue thất bại không bắt đầu file tiếp theo. Không thay entry hoặc staging Android.

`cancel_event` chia sẻ cho retry/poll/proxy; engine gọi cancel, đợi worker, release phần held chưa thành công. Request đã gửi có thể kết thúc trước khi dừng xong. Không bắt đầu tác vụ mới khi worker còn sống. Tiếp tục kiểm tra nguyên text/config/fingerprint, hash từng chunk và ledger; không chốt phí lặp cùng job/chunk. Khi hết hạn, chỉ ghép lại được nếu không cần request mới và chunk paid còn hợp lệ.

EverAI `_process_single_chunk`: mặc định 5 lần thử, delay 3 giây giữa lần, mỗi lần client/profile mới; lỗi quota theo rotate_on_429, lỗi mạng có thể force rotate. Không tự fallback API sang direct khi đã cấu hình proxy nhưng không có tuyến. Tải audio direct là tùy chọn riêng. Provider mới override method này, **không chạy vòng retry/rotate/counter của EverAI**; router upstream có xử lý riêng, cần đọc nhánh thực tế.

## Nghe mẫu và audio

EverAI `_preview` → `voice_demo_path` kiểm tra manifest/hash → player offline; không engine/network/license. Không tự gọi mạng nếu mẫu thiếu. Provider mới `_preview` → `_preview_configured`: hash config + SAMPLE_TEXT, dùng cache nếu còn; nếu chưa có chạy job preview bền vững riêng, có phí. Cache khoảng 20 mẫu; không xóa 49 mẫu nguồn. Nghe mẫu giữ kết quả chính, Nghe kết quả trở về file vừa tạo.

EverAI concat dùng stream copy, không nối byte MP3. Provider mới chuẩn hóa mono/44,1 kHz/128 kbps; timeline dùng atempo/adelay/amix, có tái mã hóa. Không coi hai đường audio có hợp đồng byte-identical.

## Store và ranh giới dữ liệu

StudioStore lưu settings/draft/recent/proxy_traffic JSON atomic; `store-lock.sqlite3` chỉ khóa đọc–sửa–ghi lịch sử/lưu lượng liên thread/process. Không dùng snapshot cửa sổ cũ ghi đè các thay đổi mới. Settings/session/proxy form có cơ chế riêng, không suy diễn mọi file JSON đều được SQLite khóa.

Proxy mặc định là một `ProxyItem` tạm do `ProxyCatalog.get_active_proxy()` dựng khi không có mục catalog hợp lệ đang active. Nó không được ghi vào `proxies.json`, không xuất hiện trong danh sách UI và địa chỉ được che trong trạng thái/kết nối/log cần hiển thị. Proxy mới hoặc proxy import vào catalog mặc định chưa active; chỉ mục người dùng kích hoạt mới được ưu tiên thay mặc định. Khi mục active bị xóa hoặc không còn hợp lệ, luồng tạo voice tự quay về proxy builtin. Proxy luôn bật trong settings; nút bật/tắt cũ đã bỏ. Retry không tự chuyển sang mục catalog khác chỉ vì lỗi, còn rotate URL của mục custom vẫn theo cấu hình mục đó.

Session EverAI chỉ RAM; ElevenLabs key có lưu settings local; draft/job chứa text. Log che bí mật nhưng JSON dữ liệu cần được bảo quản như dữ liệu người dùng. Android dùng cùng lõi qua adapter có điều kiện, thay UI/player/device/transport/crypto/FFmpeg; không import Tkinter vào APK. Chi tiết dữ liệu ở [OPERATIONAL_GUIDE.md](OPERATIONAL_GUIDE.md).
