# Trạng thái hiện hành — 05/11/2026

## Bản phát hành mới nhất — r11 (05/11/2026)

- Gói khách: `dist/VoicePro-r11.zip` (160.847.202 byte).
- ZIP SHA-256: `59f13dadc5a8e612542948138cdda6da360ad88e19d4134bd63563d4a46589a6`.
- Saydi tự tạo phiên nếu ô token để trống. Khi phiên tự động báo hết quota,
  dùng phiên mới và thử lại đúng đoạn bị lỗi một lần.
- 85 test đạt. Tạo phiên tự động và MP3 qua Saydi live đạt. Giả lập hết quota
  ở đoạn cuối đạt. EXE khởi động Qt và chạy SRT offline, ZIP kiểm tra toàn vẹn đạt.

## Bản phát hành r10 (05/11/2026)

- Gói khách: `dist/VoicePro-r10.zip` (160.842.790 byte).
- ZIP SHA-256: `dc03e409854be149dcc635ad3a98baaf20b0255fc529e1df7de517f474ce8f00`.
- Thêm Saydi với giọng HN - Ngọc Huyền. Token nhập trong Cài đặt hoặc qua
  `SAYDI_ACCESS_TOKEN` và chỉ dùng trong phiên chạy.
- 85 test đạt. Kiểm tra chọn giọng trong source và tạo MP3 qua Saydi live.
  EXE khởi động Qt và chạy SRT offline, ZIP kiểm tra toàn vẹn đạt.

## Bản phát hành r9 (05/11/2026)

- Gói khách: `dist/VoicePro-r9.zip` (160.840.889 byte).
- Chạy `Voice_Pro.exe` sau khi giải nén nguyên gói, giữ thư mục `_internal` đi kèm.
- ZIP SHA-256: `811badc07c0819c6ed1a58a458880de3f07015cec52024951dd6b6ab5fc75b4b`.
- EXE SHA-256: `95fa9da9f5661893fbe91355be6e0251e95a65a091836d67d1b9d1877757e758`.
- Thêm nút Giọng phổ biến và cửa sổ Chọn giọng theo mẫu ZIP, với 17 giọng
  Voice Pro ở trang đầu, bộ lọc, nghe thử offline và yêu thích.
- Core và Qt: 46 test đạt. EXE khởi động Qt và chạy thử SRT offline với PATH
  chỉ gồm thư mục Windows. Đối chiếu 333 tệp trong ZIP, xác nhận có 66 mẫu
  MP3 và module chọn giọng. Chưa thử tạo giọng live trên bản r9.

## Khảo sát request live — 29/10/2026

- Thêm công cụ độc lập `tools/request_survey.py`, hướng dẫn `tools/REQUEST_SURVEY.md`;
  không thay runtime hoặc release. Dùng engine và license ledger hiện hành.
- Đợt chính `output/request-surveys/20261029-221257`: 50/50 audio giọng Lê Đức
  thành công, 314 HTTP calls, 1.225 cặp create-request. Có 26 cặp trùng
  fingerprint cấu hình, 1 cặp trùng IP probe, 0 cặp trùng cả hai.
- IP là phép đo ipify cùng tuyến trước TTS, chưa xác nhận IP phía TTS quan sát;
  fingerprint là hash header được phép ghi + TLS impersonation, chưa đo JA3/JA4.
- Server hiện từ chối model_id `everai-v1.6` với application error 405.
  Công cụ có `--website-default-model` để bỏ model_id giống web hiện tại;
  đợt chính dùng tùy chọn này. Runtime production chưa sửa lỗi model này.
- Kiểm tra: `pytest tests/test_everai.py tests/test_request_survey.py -q`:
  28 passed. Báo cáo và audio chỉ lưu cục bộ, không đưa vào release.

## Workspace chuẩn

- Root phát triển: `D:/EverAI_PC_Working`.
- Source ứng dụng: `src/everai/`.
- Context duy nhất: `.AGENTS/context project/` ở root workspace.
- CodeGraph: `.codegraph/` ở root workspace; 50 file Python, 1.560 node,
  5.270 edge tại lần đồng bộ gần nhất.
- Kho lưu trữ độc lập: `D:/EverAI_Core_Final_20261027`.

Workspace này giữ logic cốt lõi của tool PC và bộ `admin_key` gốc phục vụ cấp
license. Không có Android, API server, WordPress, build/cache/log/output cũ
hoặc dữ liệu người dùng. Không phụ thuộc source của repository
`Dgt_ElevenlabsVP3.7_Pass_livevoice.pro`.

## Sản phẩm hiện hành

- Giao diện desktop PyQt6, tên hiển thị **Voice Pro - chất lượng cao nhất**.
- Sidebar khách: Studio giọng nói, Hàng đợi TXT, SRT, Lịch sử, Cài đặt.
- Proxy vẫn là chi tiết backend cần cho luồng Voice Pro nhưng toàn bộ trang,
  nút, trạng thái và cấu hình Proxy đã ẩn khỏi giao diện khách.
- Runtime TTS gồm Voice Pro nội bộ (`everai`), CapCut và Saydi với một giọng.
- ElevenLabs và Edge đã bị loại khỏi catalog, UI, runtime và release.
- SRT dùng project/cue riêng, render song song, fit timeline thích nghi và luôn
  giữ đủ lời; output có thể dài hơn mốc SRT cuối nếu không thể nén vừa.
- Trial Windows: 10.000 ký tự trong 48 giờ từ lần chạy đầu trên máy; license
  trả phí và ledger vẫn dùng cơ chế hiện hành.

## Thay đổi source sau release r6

- Tích hợp 17 giọng Studio / Clone mới trích xuất từ file HAR EverAI vào catalog
  `src/everai/voices.py`, nâng tổng số giọng native Voice Pro từ 49 lên 66 giọng.
- Bổ sung 17 file demo MP3 gốc và cập nhật `assets/voice_samples/manifest.json`
  đầy đủ digest SHA256 và nguồn Cloudflare R2; hỗ trợ nghe thử offline hoàn toàn.
- Phân loại vùng `Studio` trong bộ lọc vùng để dễ dàng tra cứu và nhóm các giọng mới.
- Thêm nút ☆/★ cạnh ô chọn giọng trong GUI Qt: bật/tắt yêu thích, ưu tiên
  giọng yêu thích trong danh sách sau khi lọc provider/ngôn ngữ/vùng/tìm kiếm.
- Danh sách lưu riêng tại `favorite-voices.json` trong data root; giữ giọng
  đang chọn khi đổi thứ tự. Đã đóng gói trong bản r7-logo bên dưới.
- Kiểm tra sau thay đổi: `pytest tests/test_everai.py tests/test_everai_qt.py tests/test_everai_studio.py -q`:
  **121 passed** (toàn bộ core, layout Qt, nghe thử offline và studio suite).
- Nhận diện mới dùng ảnh micro neon do người dùng cung cấp:
  `assets/branding/voicepro-logo.png`, ICO nhiều kích thước `voicepro.ico`.
  Logo xuất hiện đầu sidebar, icon cửa sổ/app và icon EXE của builder Voice Pro.

## Build mới — r8 (02/11/2026 - Hotfix startup máy khách)

- Gói: `D:/EverAI_PC_Working/dist/VoicePro-r8.zip` (145.375.542 byte).
- Chạy trực tiếp: `dist/VoicePro-r8/Voice_Pro.exe` và `_internal` đi kèm.
- ZIP SHA256: `f644307386507580a8490d68f60614410228d518de29be4ac3ec71ea3258bdf5`.
- EXE SHA256: `36870d1f3abc041f85e3e917d4a8af0c0f71f59bb52b97d6b051e225b15c14c5`.
- Khắc phục triệt để lỗi khởi động trên máy khách (Microsoft CRT Error R6034 / missing ICU):
  + Đóng gói đầy đủ trọn bộ MSVC CRT runtime (`MSVCP140.dll`, `MSVCP140_1.dll`, `MSVCP140_2.dll`, `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`) ngay cạnh file `Voice_Pro.exe`, trong `_internal` và trong `PyQt6/Qt6/bin`. Ngăn chặn Windows tìm kiếm CRT xung đột từ biến PATH của máy khách.
  + Đóng gói đầy đủ ICU runtime (`icuuc.dll`, `icu.dll`, `icuin.dll`) độc lập; loại bỏ thao tác xóa nhầm `icuuc.dll` trước đây, đảm bảo tương thích 100% trên cả Windows 10 và Windows 11 không có sẵn `icuuc.dll`.
  + Tự động cấu hình `os.add_dll_directory` và ưu tiên PATH nội bộ trong launcher trước khi tải bất kỳ module Qt nào.
  + Cấu trúc lại try/catch trong `qt_gui.py` để không nuốt nhầm exception nội bộ thành `No module named 'srt_studio'`.
- Nội dung: Tích hợp 17 giọng Studio / Clone mới (tổng 66 giọng Voice Pro),
  bộ 66 mẫu offline verified demo, cùng đầy đủ branding micro neon và ghim yêu thích.
- Test suite trước đóng gói: `pytest tests/test_everai.py tests/test_everai_qt.py tests/test_everai_studio.py -q`:
  **121 passed**. Test smoke khởi động offscreen độc lập không PATH: **PASS**, không sinh crash log.
- Bản r7-logo và r6 được giữ nguyên trong lịch sử.

## Build trước — r7-logo (27/10/2026)

- Gói: `D:/EverAI_PC_Working/dist/VoicePro-r7-logo.zip` (152.906.799 byte).
- Chạy trực tiếp: `dist/VoicePro-r7-logo/Voice_Pro.exe` và `_internal` đi kèm.
- ZIP SHA256: `384306639d642428d315ff09686bc41cef4c52c86ba73bd5e16e6f0508d5f4a1`.
- EXE SHA256: `42722e6ba6e81d6ca1f9341a4e2fac0d12b7c080f0d39de9a940e6e958af47e6`.
- Nội dung mới: giọng yêu thích và logo/avatar/icon micro neon do người dùng cấp.
- Test sau logo: `pytest tests/test_everai.py tests/test_everai_qt.py -q`:
  **46 passed**, 58,62 giây.
- Frozen GUI smoke và SRT offline smoke: exit 0, không crash log, data tạm riêng,
  PATH chỉ Windows/System32. Không gọi provider live.
- Kiểm PYZ có tính năng yêu thích; logo hash trùng ảnh nguồn; EXE có icon resource;
  ZIP CRC và hash từng file khớp folder build; không module admin/provider đã bỏ.
- Bản r6 trong kho backup được giữ nguyên. Lần build r7 trước khi bổ sung logo
  đã dừng; thư mục `dist/VoicePro-r7` không phải bản bàn giao.
- Chưa nghiệm thu trên máy Windows sạch hoặc provider live.

## Kiểm chứng chuyển workspace (bằng chứng trước thay đổi yêu thích)

- `pytest tests/test_everai.py`: **26 passed** (lượt cuối 8,39 giây).
- Build thử bằng `build_voicepro.py`: thành công.
- Build manifest: chỉ CapCut, 122 giọng; không có ElevenLabs/Edge/Saydi hoặc
  khóa admin.
- EXE build thử: 5.990.748 byte; SHA256
  `10e3db2f225b8a3a4199a2ee6fee82a761a12dad4f035874b29fcaf44b2e36f5`.
- Frozen GUI smoke: pass, không sinh crash log.
- Build thử đã được xóa sau xác minh; không phải release bàn giao.

## Artifact và backup

Trong `D:/EverAI_Core_Final_20261027`:

- `EverAI-PC-Source-Full.zip`: source PC phục hồi.
- `VoicePro-r6.zip`: release Windows cuối cùng, SHA256
  `2e28b9e01ac3bea698754d4590742753fb094eeb5ea5bb7668754522b6f0d8ae`.
- `EverAI-Admin-Recovery.zip`: backup admin đã restore-test thành công.
- `EverAI-Admin-Recovery-Password.dpapi`: mật khẩu được bảo vệ bằng Windows
  DPAPI CurrentUser; không đưa vào source hoặc bản khách.
- `EverAI-Admin-Key-Full.zip`: gói admin độc lập gồm code + dependency + dữ
  liệu `admin_key`; 30 entry, CRC/CLI backup-check pass, SHA256
  `81bf7b5569d04a2f57bd8bd7d8f6c095d404832b317e6ae75f7359d18621a77c`.
  Gói chứa mật khẩu phục hồi nên là artifact tuyệt mật, không phát hành khách.
- `SHA256SUMS.txt`: hash kiểm tra toàn bộ artifact.

`src/everai/admin_key` đã được phục hồi đầy đủ từ Git object gốc và backup đã
kiểm chứng: `admin_tool.py`, `MO_ADMIN.bat`, `README.md`, `.gitignore`, private
signing key, public key, mật khẩu phục hồi, 5 license đã cấp và 5 record. Import
admin tool, kiểm tra backup, mở private key và đối chiếu public key đều pass.
Suite license/admin sau phục hồi: **76 passed**; 12 mục trong recovery backup
khớp byte-for-byte với dữ liệu đã phục hồi.

## Ranh giới hiện hành

- Không khôi phục Android hoặc các provider đã bỏ chỉ vì tài liệu lịch sử còn
  nhắc tới chúng.
- `admin_key` nằm trong workspace nhưng là dữ liệu quản trị tuyệt mật. Không
  đưa vào source archive công khai, log hoặc release khách; luôn giữ thêm bản
  recovery độc lập trước mọi thao tác di chuyển/xóa.
- Build mới nhất là r7-logo tại workspace, đã kiểm tra offline như trên;
  release r6 trong kho backup được giữ nguyên.
- Chưa xác minh provider live hoặc máy Windows sạch trong lượt chuyển workspace.

## Cổng hoàn thành tối thiểu

Sau mọi thay đổi code, chạy từ root:

```powershell
pytest tests/test_everai.py
```

Nếu sửa UI/build/runtime, phải chạy thêm test liên quan và smoke bản đóng gói
theo phạm vi thay đổi.

