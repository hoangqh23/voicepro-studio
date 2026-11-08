# Độc lập và đóng gói Windows

## SRT project desktop 26/10

`srt_studio/` là package nội bộ bắt buộc trong hai builder. Không cần dependency
ngoài mới; parser/store dùng stdlib, audio dùng FFmpeg và wave, panel dùng PyQt6.
WAV player chọn MCI waveaudio; MP3 giữ mpegvideo. Giữ standalone/package import,
không nhập src/core/src/utils. Project runtime nằm data root, không đóng vào gói.

Verifier bản fast đối chiếu thêm client, audio_player và tám module srt_studio
(19 module tổng, gồm route). Client Voice Pro đối chiếu sau branding đúng builder;
chạy `--everai-srt-smoke` trong EXE với PATH tối thiểu và thư mục dữ liệu tạm.
Diagnostic chỉ sinh grant/key test và audio tổng hợp trong TemporaryDirectory,
không đọc/ghi ví production, không gọi mạng và không bỏ qua license core.
Hai cue tạo 144.000 frame WAV rồi xuất lại MP3 không TTS. Report/ZIP/guide tại
artifact SRT nêu trong CURRENT_STATE; baseline PyQt6-dark vẫn giữ nguyên.

## Desktop Qt 26/10

Entry mới yêu cầu `PyQt6==6.11.0` (máy build Qt6 6.11.2), không dùng Tk runtime.
CustomTkinter còn trong requirements để test legacy, không đóng vào EXE Qt.
Hai builder stage `qt_gui.py`/`qt_theme.py`, import entry Qt và exclude tkinter,
_tkinter/customtkinter; không cần Tcl/Tk staging. Qt platform plugin Windows
được PyInstaller đóng gói. Các đoạn Tk bên dưới là baseline cũ.
Verifier kiểm chín module PYZ/source và Qt offscreen startup ngoài worker,
Node/FFmpeg/demos/ZIP. Xem QT_UI_DESIGN.md và CURRENT_STATE cho kết quả thực tế.

## Phạm vi độc lập

`src/everai` không import `src/core`/`src/utils`, không cần automation Chromium/DrissionPage của phân hệ ElevenLabs gốc. Tuy nhiên source cần Python/Tk và dependency Python, Node/dependency JS cho provider mới, FFmpeg và assets. Không dùng câu “zero external dependencies” để mô tả môi trường source.

Chạy trong module:

```powershell
python -m pip install -r requirements.txt
python run.py
```

Repo gốc có `python run_everai_gui.py`; package `python -m everai` cần parent đúng trên sys.path. Khi copy standalone, giữ runtime .py, tts/JS/catalog/node_modules, assets/license và voice_samples, FFmpeg/vendor cần dùng. Không copy admin/private data sang máy khách. Player desktop hiện Windows MCI; không cam kết tương đương Linux/macOS.

Requirements hiện tại: requests==2.34.2, curl_cffi==0.16.3, cryptography==50.0.2, customtkinter==6.0.0, edge-tts>=6.0.0,<8; build thêm pyinstaller==6.22.3. Bản Windows hiện có dùng Python 3.14.7/Node24.19.0/Edge7.3.1. Đây là snapshot môi trường đã build, không ma trận tương thích mọi phiên bản. Edge requirements là range, muốn tái lập phải đối chiếu build-report và vendor đã dùng.

## Build

Từ module, dùng thư mục release **mới** để tránh file dư của bản cũ (builder copytree dirs_exist_ok=True):

```powershell
python -m pip install -r requirements-build.txt
python prepare_ffmpeg.py --sources
python prepare_voice_samples.py
python build_portable.py --ffmpeg vendor/ffmpeg/bin/ffmpeg.exe --dist dist/release-YYYYMMDD-name
```

Các lệnh prepare có thể tải mạng nếu thiếu asset; không cần chạy khi chỉ sửa context. Node phải trên PATH máy build. Public key hiện có phải hợp lệ; nếu thiếu thì phục hồi đúng bộ cũ, không tạo khóa mới theo lời nhắc tổng quát của builder.

Builder stage allowlist Python runtime + tts/*.py; copy providers bỏ tests/maps; add Node vào everai/tts/runtime, FFmpeg vào bin, 49 MP3 theo manifest và public_keys. Frozen launcher dispatch Edge worker trước GUI để subprocess không mở cửa sổ Studio thứ hai. Collect theme/font, curl và Edge/aiohttp deps, license Python/JS/Node/FFmpeg. Tcl/Tk dạng ZIP được xử lý staging tạm khi môi trường build cần.

PyInstaller dùng **--onedir --windowed**. Chọn onedir sau lỗi onefile giải nén khi ổ TEMP đầy; không quay lại onefile chỉ vì tên hàm “portable”. EXE + `_internal` cùng các hướng dẫn/notices/source/license cần đi theo ZIP. Khách không cần Python/Node/FFmpeg đã cài, nhưng vẫn cần quyền ghi/dung lượng dữ liệu/audio.

## Xác minh và hồ sơ

Bản sửa desktop 25/10 có `tools/verify_desktop_release.py <release-dir> [--voicepro]`: đối chiếu bytecode sáu module sửa với source (có áp dụng nhãn thương hiệu), giữ nguyên `_finish` theo yêu cầu loại trừ thông báo lỗi provider, hash toàn runtime và 49 mẫu, thử frozen worker `--help` và FFmpeg/Node với PATH tối thiểu/data tạm, sau đó đóng ZIP và kiểm CRC/hash. Đây là kiểm tra offline; `gui_smoke_tested:false` và `live_provider_tested:false`, không thay nghiệm thu tương tác GUI/máy sạch. ZIP chứa `release-check.json`; hash ZIP ở file `.verification.json` cạnh release. Dùng release mới, không ghi đè bản khách đang mở.

Builder kiểm tra archive có GUI/engine/license/TTS/Edge, asset Node/JS/catalog, public key, FFmpeg và đúng 49 mẫu; không src.*, admin tool/private key/password/license/ví/data/log riêng. FFmpeg mặc định có upstream pin/provenance; custom --ffmpeg khác đường vendor được báo provenance chưa xác minh.

build-report.json có Python/packages/hash EXE/FFmpeg, onedir/keep_directory, key IDs công khai, số mẫu/byte, provider catalog/language/model list, Node/license hash và runtime_files_sha256. Bản hiện tại có thêm release-check.json/HUONG_DAN.txt. **build_portable.py không tự chạy toàn bộ live smoke, so source/PYZ hoặc tạo ZIP giao khách**; cần bước kiểm tra/đóng ZIP riêng.

Gói hiện tại/hash chỉ ghi tại [CURRENT_STATE.md](CURRENT_STATE.md). Khi phát sinh bản mới: kiểm tra hash toàn runtime và source archive tại đúng snapshot, copy gói sang đường dẫn Unicode/data test/PATH tối thiểu, mở/đóng GUI và thử audio với runtime gói; kiểm CRC/hash ZIP. Không dùng báo cáo bản cũ cho binary mới.

Mẫu nguồn được prepare từ demo công khai, hash/nguồn trong manifest, đủ thì không tải lại; không dùng quota tổng hợp. FFmpeg vendor ghim archive digest, giữ source/recipe/notices; không bỏ qua pin khi nguồn upstream đổi. Hồ sơ inventory không tự xác nhận quyền phân phối toàn bộ dependency/demo. Máy sạch, usability và phân phối vẫn theo [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md).
