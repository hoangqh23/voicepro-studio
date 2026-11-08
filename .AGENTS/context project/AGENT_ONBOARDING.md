# Hướng dẫn agent tiếp quản

## Bắt đầu

1. Làm việc tại `D:/EverAI_PC_Working`.
2. Đọc [CURRENT_STATE.md](CURRENT_STATE.md).
3. Dùng CodeGraph trước khi dò hoặc đọc code.
4. Chỉ mở tài liệu chuyên đề liên quan từ [README.md](README.md).

## Phạm vi

- Sản phẩm là tool Windows Voice Pro/EverAI trong `src/everai`.
- Runtime hiện hành chỉ có Voice Pro nội bộ và CapCut.
- Proxy backend vẫn tồn tại nhưng UI khách đã ẩn toàn bộ cấu hình/trạng thái.
- Android, API server, WordPress, ElevenLabs, Edge và Saydi không thuộc phạm
  vi source hiện hành. Không tự khôi phục chúng từ tài liệu lịch sử.
- Workspace không phụ thuộc source hay utility của repository cũ.

## Bất biến cần giữ

- Fingerprint phải gắn với text/config/chunk version; không nới resume âm thầm.
- Chunk ghép đúng thứ tự; chỉ commit phí sau khi lưu an toàn; không tính lại
  cùng job/chunk.
- Không thêm bypass license production. Test dùng khóa/grant tạm.
- Không reset ví, đổi data root hoặc sửa/xóa dữ liệu người dùng âm thầm.
- MP3 phải được kiểm tra và ghép bằng FFmpeg, không nối byte trực tiếp.
- Qt chỉ cập nhật UI từ main thread; tác vụ nền phải đi qua signal/queue hiện có.
- Không ghi session, proxy credential, API key, private key hay mật khẩu vào
  log, context, source hoặc artifact khách.

## Khóa admin và artifact

Khóa admin gốc nằm tại `src/everai/admin_key`; backup độc lập ở
`D:/EverAI_Core_Final_20261027`. Không tự tạo, thay hoặc xoay khóa; không đưa
admin tool/secrets/records/issued/mật khẩu vào release khách. Release hiện hành
là `VoicePro-r6.zip`.

## Kiểm tra

Tối thiểu sau mọi thay đổi code, chạy từ root:

```powershell
pytest tests/test_everai.py
```

Hiện bộ bắt buộc có 26 test. Nếu sửa UI, SRT, license, player, queue hoặc build,
chạy thêm suite tương ứng và smoke artifact theo mức rủi ro. Không gọi provider
live, tiêu hạn mức hoặc dùng dữ liệu production nếu người dùng chưa yêu cầu.

## Cập nhật context

- Ghi trạng thái mới vào `CURRENT_STATE.md`, thay nội dung lỗi thời thay vì nối
  nhật ký dài.
- Ghi rõ đâu là kết quả vừa chạy, đâu là bằng chứng lịch sử.
- Source đổi không đồng nghĩa release đã đổi.
- Context chỉ nằm ở `.AGENTS/context project/` tại root workspace.

