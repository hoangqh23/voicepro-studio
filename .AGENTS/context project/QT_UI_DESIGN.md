# PyQt6 Studio — thiết kế và hợp đồng desktop, 26/10/2026

## Hướng thiết kế được chọn

Theo yêu cầu người dùng: PyQt6, hai chế độ sáng/tối, nền tối gần đen với
gradient tím–xanh rất nhẹ. Không aurora rực, không blur, không animation nền.
Mặc định tối cho cài mới; giữ lựa chọn đã lưu ở `qt-ui.json`. Không đổi data root.

Đã dùng skill local `D:/ui-ux-pro-max-skill-main/ui-ux-pro-max-skill-main/.claude/skills/ui-ux-pro-max`:
đọc SKILL, quick-reference và pro-rules; tìm design-system cho audio desktop,
UX focus/contrast và field grouping/progressive disclosure, style dark OLED.
Hai truy vấn design-system trả pattern Hero+Features+CTA không phù hợp desktop:
đã loại phần landing-page, không persist output đó. Không có stack PyQt6 trong
22 stack được hỗ trợ; triển khai Qt native dựa trên nguyên tắc UX chung đã
đối chiếu, không giả dùng WPF/web. Pro-rules là mobile, không áp đơn vị touch,
safe area hay viewport điện thoại vào app Windows.

## Token và cấu trúc

- `qt_theme.py`: semantic tokens cho cả hai theme; tối bg #0b0c10, card #13151b,
  text #eff0f8, muted #a0a7b8, accent #b0a1fa. Gradient nền #171420 → #0b0c10
  → #0b0e13; primary lavender → xanh dịu. Không gradient lên vùng đọc text.
- Segoe UI native Windows, hỗ trợ tiếng Việt; không tải hoặc đóng gói font Windows.
- Icon QPainter đồng nhất, vector high-DPI, có nhãn chữ. Không emoji navigation.
- Sidebar năm mục: Studio, TXT queue, proxy, lịch sử, cài đặt. Vùng nội dung
  và cột thiết lập qua splitter; cột thiết lập cuộn dọc, không cuộn ngang.
- Thanh tạo/dừng/tiến độ/player luôn ở đáy riêng, không đè nội dung.
- Mô hình/tốc độ/luồng/nơi xuất ở nhóm chính; cao độ/bitrate trong disclosure
  nâng cao. State gốc vẫn được snapshot/lưu, không âm thầm thay config.
  Nhãn nút đóng là **Tùy chọn nâng cao +**, nằm dưới Thư mục xuất; cột phải
  cuộn dọc để tìm khi cửa sổ thấp. Cao độ chỉ enabled với EverAI. Người dùng
  đã hỏi vị trí control sau đổi UI; hiện chỉ ghi nhận vấn đề khả năng tìm thấy,
  chưa có yêu cầu thay bố cục hoặc mở pitch cho các provider khác.
- Focus nhìn thấy cho button/input/combo/spin/editor/slider/table. Seek hỗ trợ
  phím ngoài thao tác kéo; field có accessible name. Lỗi text/token gần field,
  focus field sai, sửa field xóa marker. Không đổi thông báo lỗi provider core.
- Debounce 500 ms autosave/bộ đếm, HTTP/TTS/health/rotate ở QThread, không main UI.

## Ranh giới kỹ thuật

Entry `run.py`, `__main__.py`, repo `run_everai_gui.py` dùng `qt_gui.launch_gui`.
Qt không import gui.py/Tk/settings_dialog/license_dialog. Tk legacy giữ để
test hồi quy, không đóng vào EXE Qt. `StudioWindow` dùng StudioStore,
LicenseManager, ProxyCatalog, engine và Windows MCI hiện có.

TaskThread hoàn tất run trước xử lý finished/mở khóa/tác vụ kế. Callback engine
chỉ ghi queue; QTimer main thread đọc. Close hủy và chờ worker/network kết thúc.
Queue dùng snapshot config, không token; mỗi item dùng job/license core riêng.
Preview không thay kết quả chính. Saydi token/session chỉ RAM; API key ElevenLabs
vẫn theo hợp đồng settings cũ. CapCut/Saydi luôn direct.

## QA và giới hạn

`tests/test_everai_qt.py` chạy event loop Qt thật, wallet/grant/data tạm, audio
offline và FFmpeg thật. Bao gồm theme persistence, 900×620/1280×850/1500×950,
panel không overflow, disclosure, text validation, contrast token >=4.5:1,
provider IDs/chunk350/SRT, token không vào JSON, preview, queue, resume/cancel.
Không tuyên bố chứng nhận accessibility hoặc kiểm thử screen reader/DPI Windows.

`tools/preview_qt_ui.py` render dark/light và compact với nội dung tổng hợp và
data tạm, không đọc dữ liệu thật. Offscreen Qt không tự tìm font trên máy này:
chỉ harness nạp font Windows đã cài, production không bundle font.
`tools/verify_desktop_release.py` kiểm PYZ khớp source, runtime/demos/catalog,
frozen worker, Node/FFmpeg, Qt offscreen startup và ZIP CRC/hash. Smoke không
thay nghiệm thu tương tác EXE trên Windows sạch; không gọi provider live.

Dependency PyQt6 được ghi notices/metadata; cần rà giấy phép dependency trước
phân phối thương mại. Việc build local không tự xác nhận quyền phân phối.

### Sửa packaging ICU

Smoke frozen phát hiện DLL load failed QtCore (cửa sổ lỗi PyInstaller gây
timeout): PyInstaller lấy ICU 78 renamed `ucnv_open_78` từ PATH rồi đặt tên
icuuc.dll vào _internal; Qt cần Windows ICU ABI `ucnv_open`. Hai builder dùng
sanitize_qt_runtime bỏ DLL shadow này khỏi bundle mới, dựa ICU Windows native.
Không sửa DLL/hệ thống máy người dùng. Bản DLL sai của hai gói thử được chuyển
ra `.build-pyqt6-20261026/*-wrong-icuuc.dll` để đối chiếu, không xóa dữ liệu app.
Sau sửa, diagnostic frozen EverAI thoát 0 trong ~4 giây với PATH chỉ Windows,
data tạm và offscreen. Verifier chặn đóng gói icuuc.dll root tái xuất hiện.
