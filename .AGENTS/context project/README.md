# Voice Pro PC — bản đồ context

Context này áp dụng cho workspace `D:/EverAI_PC_Working`, chỉ gồm tool PC.
Mọi đường dẫn tương đối trong tài liệu được tính từ root workspace hoặc từ
chính thư mục context nếu là liên kết Markdown.

## Đọc bắt buộc

1. [CURRENT_STATE.md](CURRENT_STATE.md) — trạng thái source, release và backup hiện hành.
2. [AGENT_ONBOARDING.md](AGENT_ONBOARDING.md) — quy tắc tiếp quản và cổng kiểm tra.
3. [CODEGRAPH.md](CODEGRAPH.md) — cách dùng graph của workspace mới.

## Tài liệu kỹ thuật đang dùng

| Tài liệu | Nội dung |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Kiến trúc engine, GUI, job/chunk, store và audio |
| [SRT_TIMELINE_PLAN.md](SRT_TIMELINE_PLAN.md) | Thiết kế project SRT, cue, fit và xuất timeline |
| [QT_UI_DESIGN.md](QT_UI_DESIGN.md) | Hợp đồng giao diện PyQt6 |
| [USER_GUIDE.md](USER_GUIDE.md) | Cách sử dụng tool PC |
| [OPERATIONAL_GUIDE.md](OPERATIONAL_GUIDE.md) | Dữ liệu, runtime, build và xử lý sự cố |
| [INDEPENDENCE_AND_DEPLOYMENT.md](INDEPENDENCE_AND_DEPLOYMENT.md) | Dependency và đóng gói Windows |
| [ADMIN_KEY_GUIDE.md](ADMIN_KEY_GUIDE.md) | License và backup/restore admin |
| [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md) | Checklist kiểm thử/phát hành |
| [TTS_PROVIDER_MODULE.md](TTS_PROVIDER_MODULE.md) | Lịch sử/kiến trúc adapter; hiện chỉ CapCut còn hoạt động ngoài Voice Pro |

Một số tài liệu chuyên sâu có thể còn nhắc Android, API/WordPress hoặc provider
cũ để giải thích lịch sử thiết kế. Các đoạn đó không mô tả phạm vi hiện hành;
`CURRENT_STATE.md` luôn là nguồn chuẩn khi có mâu thuẫn.

Không tạo context mới bên trong `src/everai`. Mọi cập nhật tiếp theo đặt trong
`.AGENTS/context project/` ở root workspace.

