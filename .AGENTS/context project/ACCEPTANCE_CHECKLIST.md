# Checklist kiểm tra và nghiệm thu

Luôn chọn artifact/hash tại [CURRENT_STATE.md](CURRENT_STATE.md), ghi rõ phiên bản nguồn và báo cáo. Checklist này là việc phải kiểm tra khi phạm vi thay đổi, không phải danh sách đã đạt. Dùng dữ liệu/ví/khóa test tạm; gọi live và dùng license thật theo tác vụ đã được yêu cầu.

## Kiểm tra mã và tài liệu

- [ ] Chỉ sửa đúng phạm vi, giữ thay đổi/dữ liệu người dùng; không src/core/src/utils, không đưa bí mật vào context/log/build.
- [ ] Graph đúng root/sync không stale; dùng đường file/symbol khi trùng engine/process. Không lấy graph làm bằng chứng live.
- [ ] Chạy bắt buộc repo gốc: `python -m pytest tests/test_everai.py -q` (hiện26 ca).
- [ ] Runtime đổi: chạy bốn suite EverAI/player/studio/license liên quan. Android đổi: android/tests và device/package checks phù hợp.
- [ ] Test đủ trường hợp có ảnh hưởng: thứ tự/concurrency1/3/8, retry/cancel, ghép lỗi, chunk hỏng, crash hai điểm file/DB/manifest, hai cửa sổ/process, expiry/resume, proxy active/lịch và JSON hỏng.
- [ ] Context link nội bộ còn đúng; trạng thái/test/source/EXE/APK tách rõ. Không để build cũ lại mang nhãn hiện hành.

## Windows đóng gói

- [ ] Voice Pro mở lần đầu/khôi phục/mở lại settings với nhãn Voice Pro/VoicePro nhưng snapshot/job vẫn dùng ID `everai`/`everai-v1.x`; mẫu EverAI vẫn offline.
- [ ] Dừng/timeout giữa bước FFmpeg thực sự kết thúc child, giữ chunk paid, release held và resume không tạo/trừ phí lặp.
- [ ] Tiếp tục sau khi queue xong phục hồi đúng job; hỏi trước khi thay draft/config; bỏ qua file cuối dọn ngay banner; queue đang dở ưu tiên job của queue.
- [ ] SRT nhiều cue/cue dài: GUI và engine cùng số ký tự/chunk. Chạy suite `tests/test_everai_desktop_fixes.py`.
- [ ] Giữ nguyên thông báo lỗi provider nếu người dùng chưa thay đổi yêu cầu loại trừ phần đó; đối chiếu `_finish` với baseline đúng.

- [ ] Build vào thư mục mới, giữ EXE + `_internal`; không chỉ copy EXE. Public key cũ đúng, 49 mẫu/hash, Node/Edge worker/FFmpeg/JS/JSON/license đủ.
- [ ] Archive/runtime không admin/private key/password/issued/records/ví/license/data/log/output riêng hoặc src.*; đối chiếu code/asset với snapshot.
- [ ] Report/hash runtime và EXE; ZIP kiểm CRC và hash file trong gói. Không ghi đè binary người dùng đang mở.
- [ ] Copy cả gói sang đường Unicode/PATH chỉ Windows, bỏ PYTHONHOME/PYTHONPATH, data/ví test riêng. Kiểm LOCALAPPDATA và portable, thiếu dung lượng/TEMP/đường read-only, mở/đóng exit sạch.
- [ ] Provider runtime trong gói tạo MP3/preview/SRT đúng; ElevenLabs live với key/quyền voice/model thực khi được yêu cầu. Proxy end-to-end từng provider, không suy ra pass từ adapter/mock.

## UI và chức năng trên Windows sạch

- [ ] Máy/VM chưa cài Python/Node/FFmpeg, ghi OS/độ phân giải/DPI/hash. PATH tối thiểu trên máy dev không thay cổng này.
- [ ] Người mới không được hướng dẫn trước: nhận ra nhập/chọn giọng/Tạo khoảng30 giây; ghi thời gian thực, vướng mắc, nghe/mở kết quả và làm bài thứ hai.
- [ ] Ba tab/Cài đặt giữ draft/tham số/kết quả; thanh Tạo/Dừng/player luôn thấy; đóng/Hủy/Lưu đúng, keyboard/focus/900×600/1366×768/scale100/125/150%.
- [ ] Provider/ngôn ngữ/model/voice lọc đúng; popup8 dòng cuộn được; control khóa theo provider/busy; không mô tả mọi metadata EverAI đã phân loại chuẩn.
- [ ] EverAI49 mẫu offline không mạng/không phí; thiếu mẫu không tự gọi mạng. Provider mới render mẫu có phí/cache; Nghe kết quả giữ file chính.
- [ ] TXT/SRT replace/append/cancel và undo; EverAI lời thoại liên tục, provider mới timeline gap/overlap/fit/duration, nhiều hơn24 cue.
- [ ] Tạo nhiều luồng giữ thứ tự/progress/resume; 100% sau lưu. Dừng chờ worker; đổi nơi xuất tiếp tục ghép không tạo lại paid chunk.
- [ ] Player play/pause/seek/resume/EOF/fallback; đường Unicode; trùng tên không ghi đè, lịch sử không xóa MP3.
- [ ] Proxy CRUD/kích hoạt/lưu/nhập TXT/check/rotate/lịch; proxy builtin ẩn luôn làm fallback khi chưa có custom active; proxy mới/import chưa tự active; không còn checkbox bật proxy. Tải trực tiếp là tùy chọn riêng của EverAI; HTTP/HTTPS provider mới.
- [ ] Byte/request/CSV/reset/persist đúng phạm vi; không hứa đo TLS/cước hoặc số liệu provider mới chưa tích hợp.
- [ ] Mã máy/key/file/import trùng/sai chữ ký/sai máy/v1/v2/hết hạn/gia hạn/thiếu số dư/backup ví; request mới chặn đúng, giữ số dư và audio cũ.
- [ ] Admin log xác thực trước mở, redaction, support không key, notices truy cập được; JSON/DB lỗi có hướng giữ/khôi phục dữ liệu.

## Android và phân phối

- [ ] Dùng checklist thiết bị/runtime/lifecycle/audio/SAF/update trong [ANDROID_BUILD_AND_TEST.md](ANDROID_BUILD_AND_TEST.md); không lấy instrumentation APK cũ xác nhận APK mới.
- [ ] Signature/ABI/16KB/hash/report của đúng APK; giữ key ký cũ; release_ready còn false khi chưa có biên bản.
- [ ] Hồ sơ dependency/FFmpeg/Node/JS/Android/source/notices và quyền tái phân phối49 mẫu được xác nhận cho kênh giao khách.

## Biên bản tối thiểu

Ngày/người thử; artifact/path/SHA256; source snapshot; OS/API/ABI/page size/màn hình/scale; data test; provider/model/voice; ca chạy và expected/actual; pass/fail/chưa chạy; lỗi/tái hiện; report liên quan. Không ghi credential/cookie/key/văn bản riêng. Chỉ đóng cổng nghiệm thu theo bằng chứng đúng môi trường và đúng artifact.
