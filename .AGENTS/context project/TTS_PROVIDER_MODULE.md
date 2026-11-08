# TTS provider desktop

## Hợp đồng hiện hành — 27/10/2026

Desktop chỉ đăng ký hai engine: Voice Pro nội bộ (`everai`) và CapCut. Các
provider `elevenlabs`, `edge`, `saydi` đã bị loại khỏi catalog, UI, credentials,
preflight và runtime. `split_voice_id` vẫn nhận ba prefix cũ để job/project lịch
sử nhận lỗi migration rõ ràng; không tự đổi sang Voice Pro. `tts/service.py`
chỉ route CapCut trực tiếp và build Voice Pro không còn đóng gói Node/Edge.

Các phần Edge/ElevenLabs/Saydi phía dưới là hồ sơ lịch sử của release cũ,
không phải hướng dẫn vận hành source hiện hành.

## Saydi AI — Ngọc Huyền, 25/10/2026

Provider `saydi` bổ sung giọng `saydi|vi-hn-ngoc-huyen`, tách khỏi Ngọc Huyền
của EverAI. Client tại `tts/saydi.py`, POST `/api/tts/demo` với JSON
`text`, `sample=vi-hn-ngoc-huyen`, `output_format=mp3` theo tài liệu người dùng
đính kèm. Trang giọng công khai đã đối chiếu tồn tại. Chưa có token hợp lệ để
kiểm chứng TTS live; không xem hợp đồng cung cấp là tài liệu API chính thức.

GUI có lựa chọn saydi và ô Bearer token che ký tự, chỉ giữ RAM. Người dùng
nhập token từ phiên Saydi hợp lệ của chính mình; không tự lấy token, đăng nhập
hay giải Turnstile. Token không nằm trong settings/draft/queue/job/fingerprint,
argv hoặc Node stdin. Cần nhập lại khi mở app; đổi token có thể tiếp tục job
cùng text/config. Preflight chặn token trống/sai định dạng trước gọi mạng.

Saydi luôn trực tiếp, `trust_env=False`, không dùng catalog/env proxy; mỗi
chunk có session riêng. Chunk tối đa 350 ký tự là lựa chọn bảo thủ dựa trên
tài liệu đính kèm, không tuyên bố là hạn mức chính thức. GUI/engine chia chung
quy tắc 350 cho text/SRT, fingerprint có max_chunk_chars, dùng license ledger,
resume và ghép/timeline hiện có. Tốc độ chỉ áp một lần bằng FFmpeg, MP3 chuẩn
mono/44,1kHz/128kbps như provider khác. Mẫu Saydi render có tính hạn mức/cache.

HTTP connect/read timeout 10/30 giây; tối đa ba lần cho lỗi mạng, HTTP429/5xx,
backoff có hủy và Retry-After tối đa 30 giây. Không retry 401/403/4xx/redirect;
không chuyển tiếp Authorization theo redirect. Response phải đúng loại audio,
có MP3 header, 128 byte–32MiB; ghi .part/fsync/replace, hủy/lỗi dọn .part và
giữ output cũ. Thông báo không đưa token/response body nhạy cảm vào log/chat.
Không dùng engine độc lập/fingerprint chỉ theo text hoặc fallback nối byte MP3
trong mẫu đính kèm. Kiểm thử offline tại `tests/test_everai_saydi.py`.

## Cập nhật CapCut trực tiếp — 25/10/2026

CapCut hiện đi qua `tts/capcut.py`, không còn dùng nhánh web CapcutTTS trong
`upstream.cjs`. `runner.cjs` chỉ nhận Edge/ElevenLabs. Adapter đối chiếu protocol
với `D:/VIRAL_REUP (1)/VIRAL_REUP/resources/app-src/capcut_tts.pyc`: base
`editor-api-sg.capcutapi.com`, `common_task/new` → `common_task/query`, task v3,
SSML/platform sami, app 8.7.0/pf3, RSA PKCS1v15 payload và MD5 header/body.
Không import/chạy app tham chiếu hoặc dùng tài khoản/device/license của app đó.

Catalog gốc gồm 122 voice/resource tại `tts/providers/capcut_voices.json`, được
đọc vào `TTSService.VOICES`; Edge 322 và ElevenLabs 255 giữ catalog riêng cũ.
Không kèm thư viện ElevenLabs tùy chọn của app tham chiếu trong provider CapCut.
ID CapCut mới giữ raw speaker ID của catalog task; job cũ với prefix web cũ
phải chọn giọng/tạo job mới, không âm thầm thay giọng hoặc fingerprint để resume.

CapCut bỏ qua proxy catalog và proxy truyền explicit; requests.Session có
`trust_env=False`, xóa proxies, dùng trực tiếp cho tạo/poll/tải MP3. Không cần
Node cho preflight CapCut. Giữ text nguyên bản/escape XML, SSML rate=1.0;
tốc độ chỉ áp một lần trong bước chuẩn hóa FFmpeg. Giữ job/license/chunk/resume
và SRT của Studio. Poll có thời hạn 90 giây, dừng khi task thất bại hoặc kết quả
không khớp; hủy không publish audio mới. Timeout từng HTTP tối đa 60 giây.

`tests/test_everai_capcut.py` kiểm tra offline chữ ký, SSML, proxy bypass,
task/poll/download, lỗi, hủy/timeout và tốc độ. Kiểm tra offline không chứng minh
dịch vụ trực tuyến hiện cho phép từng giọng/tài khoản; xem CURRENT_STATE cho
artifact và kết quả kiểm thử thực tế. Các mô tả CapCut web/175 giọng ở phần
lịch sử dưới đây đã được thay thế bởi cập nhật này.

Đối chiếu source 23/10/2026. Được tích hợp trực tiếp vào Studio từ closure TTS của bundle ở `D:/ezmaxsub`; runtime đã tách, không import/chạy file tại đó. Bản Windows/hash/bằng chứng: [CURRENT_STATE.md](CURRENT_STATE.md). APK hiện có chưa tích hợp module này.

## Ranh giới code và runtime

| File | Hợp đồng |
| --- | --- |
| `tts/service.py` | TTSService, catalog, preflight, Node subprocess, proxy, chuẩn hóa MP3/publish |
| `tts/engine.py` | MultiProviderTTSEngine kế thừa job/license/resume/executor, override chunk render và ghép SRT |
| `tts/srt.py` | parse_srt, assemble_timeline |
| `tts/languages.py` | Tên ngôn ngữ, model ElevenLabs và danh sách mã ngôn ngữ theo model |
| `tts/edge_worker.py` | Frozen dispatch `--everai-edge-worker`, chạy Communicate bằng chính EXE |
| `tts/providers/runner.cjs` | Một request/process, whitelist ba provider, stdin JSON, phân loại lỗi |
| `tts/providers/upstream.cjs` | Closure router/renderer khoảng 33 KB; còn tên biến rút gọn từ upstream |
| `tts/providers/voices.json`, `node_modules/` | Catalog 752 voice và dependency JS local/license |
| `tools/extract_tts.cjs` | Tái trích closure/catalog từ bundle local, marker SHA256 source; không cần cho runtime |
| `tools/update_edge_catalog.py` | Gọi dịch vụ cập nhật riêng Edge, giữ CapCut/ElevenLabs, từ chối catalog thiếu trường/quá ít voice |

Không tái chạy extract chỉ để sync context: nó sinh lại catalog/closure, có thể làm mất cập nhật Edge/patch hiện tại. Công cụ lấy source/destination qua argv, format phụ thuộc bundle gốc; phải kiểm tra lại sau tái trích.

Source cần Node trên PATH; TTSService ưu tiên `tts/runtime/node.exe` nếu có. Edge dùng sys.executable + vendor/tts_python hoặc edge-tts đã cài. EXE có Node và Edge worker, không cần runtime ngoài. Runner/worker/FFmpeg Windows ẩn console.

## Catalog, lựa chọn và config

- CapCut 175 voice, giữ prefix `vn:`, `bv:`, `icl:` và ID gốc; không đổi chữ hoa/thường ID tùy tiện.
- Edge 322 voice, 75 mã ngôn ngữ theo catalog hiện có; công cụ update mới là thao tác mạng, không tự chạy khi đọc context.
- ElevenLabs 255 voice; ngôn ngữ nguồn voice không giới hạn ngôn ngữ model. Model mà code cho chọn: `eleven_v3`, `eleven_v4`, `eleven_flash_v2_5`, `eleven_multilingual_v2`.
- ID nội bộ mới: `provider|voiceId`. `split_voice_id` chỉ nhận ba prefix; ID không có prefix hợp lệ đi nhánh EverAI.
- `_snapshot` giữ model trong model_id; ElevenLabs có ngôn ngữ thì dạng `model@language`. TTSService validate model/language theo languages.py rồi tách vào request.
- Pitch và bitrate EverAI bị khóa trên UI khi provider mới; output ba provider chuẩn hóa MP3 mono/44,1 kHz/128 kbps. Volume hiện cố định 100; không mô tả pitch/volume/bitrate là các nút điều khiển provider tương đương EverAI.

Catalog/model list mô tả lựa chọn code hiện có, không bảo đảm API/model/voice được provider cho dùng ở mọi tài khoản. ElevenLabs chưa live trong hồ sơ hiện tại.

## Pipeline một chunk

1. MultiProvider `_process_single_chunk` kiểm tra cancel/license, lấy proxy hiện tại; proxy builtin luôn có sẵn khi catalog chưa có mục custom active.
2. `TTSService.preflight`: provider hợp lệ, có Node, ElevenLabs có key, Edge có dependency, có FFmpeg. `synthesize` kiểm tra speed finite 0.5–2.0 và voice trong catalog.
3. Proxy adapter chỉ nhận HTTP/HTTPS, URL-encode user/password. Proxy builtin mặc định của EverAI là SOCKS nên không chạy trực tiếp qua adapter provider mới; muốn dùng CapCut/Edge/ElevenLabs cần custom HTTP/HTTPS active. Không suy ra SOCKS của catalog EverAI chạy được ở provider mới.
4. Gửi config/text/key qua stdin JSON tới `node runner.cjs`, không đưa API key vào argv/manifest/job/log. Key **được lưu settings.json local**, ô password UI không có nghĩa file đã mã hóa. Edge child nhận text/voice/proxy qua argv riêng; không tuyên bố mọi dữ liệu đều chỉ qua stdin.
5. Runner chặn provider ngoài whitelist, tắt console provider và chỉ trả success hoặc category/status. Không serialize axios error vì có thể chứa header/key. Categories authentication/payment/rate_limit/provider được Python chuyển thành lỗi ngắn.
6. Proxy: axios qua env rõ ràng, WebSocket qua HttpsProxyAgent, Edge thêm --proxy. Cache cookie CapCut trong TEMP/EverAIStudio-TTS/<hash tuyến>; tách theo proxy/direct, không dùng cache ezmaxsub.
7. Router tạo audio; Python FFmpeg chuẩn hóa, kiểm tra file, `os.replace` vào chunk output. Khi cancel trong lúc Node chạy, Windows taskkill /T /F; các bước FFmpeg dùng subprocess timeout, không bảo đảm ngắt tức thì mọi pha.

ElevenLabs V3/Flash/Multilingual đi Text-to-Speech; V4 đi Text-to-Dialogue. Runner chặn native speed 0.7–1.2 cho nhánh TTS, dùng FFmpeg bù phần còn lại để đạt UI 0.5–2.0; V4 chỉnh tốc độ hậu kỳ. Đây là payload code hiện có, cần live test riêng từng model/tài khoản.

## SRT timeline desktop hiện hành, 26/10

Qt import .srt dùng `srt_studio/` cho cả EverAI và mọi provider. Mỗi cue có job
con và raw output riêng; nhiều chunk trong một cue được ghép trước khi fit.
`MultiProviderTTSEngine(input_mode='text')` tắt heuristic '-->' trong các job
con, giữ trần Saydi350 và pipeline tốc độ/provider hiện có. Cue timestamp,
policy fit và export không vào fingerprint child. Đổi chúng chỉ hậu kỳ raw,
không gửi lại TTS/không cần token Saydi mới. Raw mất/hỏng thì báo Tạo lại có phí.
PCM48k16, auto1.35/force4, không cắt lời, cue dài đẩy tuần tự, chặn overlap
speech; chi tiết ở
[ARCHITECTURE.md](ARCHITECTURE.md) và [USER_GUIDE.md](USER_GUIDE.md).

## SRT timeline legacy (Tk/job cũ)

Chọn provider mới rồi nạp SRT giữ raw timestamps trong editor; `-->` kích hoạt parse SRT. Parser bỏ index/tag HTML, yêu cầu cue có lời và end>start, chia lời mỗi cue bằng chunker lõi.

Engine lưu `srt_groups` với start/end/indices. Khi ghép: concat các chunk trong cue → đo duration → chỉ tăng tempo nếu quá thời lượng cue → adelay theo start → amix giữ khoảng trống/chồng cue → apad/atrim tới end xa nhất. Batch tối đa 24 cue, trộn stems tuần tự để giới hạn input/command Windows. Output mono/44,1 kHz/128 kbps, có tái mã hóa.

Mỗi bài một voice, chưa có cast/per-cue voice hoặc fit policy auto/manual nâng cao. EverAI SRT vẫn chỉ lấy lời thoại liên tục. Source/EXE desktop-fixes-r2 dùng chung `split_srt_chunks` cho GUI và engine, chia từng cue thay vì gộp lời trước khi đếm; số ký tự/chunk khớp pipeline. Ví theo chunk engine thực tế. `run_audio_command` theo dõi FFmpeg với cancel/deadline, dùng cho chuẩn hóa, concat cue và mix/timeline.

## Phần dùng chung và phần riêng

Kế thừa reservation/ledger/license, fingerprint, manifest, tiến độ, executor, thứ tự, resume, publish và lịch sử/player. **Không kế thừa vòng retry/rotate/device profile/đo HTTP của `_process_single_chunk` EverAI** vì override method đó. Cấu hình randomize_device/download_via_direct_ip không được service mới dùng như client EverAI. Router upstream thử tối đa **3 lần**, kiểm tra header/audio tối thiểu, phân loại lỗi không retry (voice/text/key/quyền/payment…), chờ backoff có jitter và xét Retry-After (giới hạn 30 giây). Không tự xoay catalog proxy hay sinh device profile giữa các lần đó.

Router có bước trim khoảng lặng đầu/cuối; CapCut còn chỉnh tempo hậu kỳ. Input không còn chữ/số có thể được router ghi WAV im lặng cục bộ thay gọi provider, sau đó Python chuẩn hóa MP3. Đây vẫn là chunk render trong job và đi qua commit phí Studio; số ký tự ví không phải hóa đơn request của provider.

Provider mới chưa tích hợp byte/request counter vào bảng proxy; số 0 không có nghĩa không phát sinh mạng. End-to-end proxy chưa live. Mẫu render dùng job/cache/license như bài mới; cache có sẵn phát lại miễn phí. Các test tạm Python9/Node6 từng pass nhưng đã dọn theo yêu cầu người dùng; báo cáo không phải bộ test script đang còn trong source.
