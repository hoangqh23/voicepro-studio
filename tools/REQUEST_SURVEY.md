# Khảo sát request Voice Pro

Chạy từ root bằng `python tools/request_survey.py --count 50 --voice vi_male_leduc_mb --website-default-model`.

Đây là test live, có sử dụng dịch vụ và hạn mức ứng dụng. Script dùng engine,
license ledger và proxy đang chọn trong data root hiện tại. Không dùng khóa admin,
không thay cấu hình ứng dụng. Session đăng nhập chỉ có trong RAM GUI không được
đọc từ GUI; bài đo chạy với session mới. Một lượt chỉ gửi một lần create-request;
retry GET lấy CSRF và polling vẫn theo client hiện hành.

`--website-default-model` bỏ trường model_id khi gửi, giống web hiện tại.
Không dùng tùy chọn này thì giữ nguyên model cấu hình của ứng dụng. Lượt khảo sát
ngày 29/10/2026 phát hiện máy chủ từ chối `everai-v1.6` với mã ứng dụng 405.

Kết quả nằm trong `output/request-surveys/<thời gian>/`:

- metadata.json: nội dung, giọng, cấu hình và giới hạn phép đo.
- requests.jsonl / requests.csv: từng HTTP call gồm probe, CSRF, create, poll, download.
- runs.jsonl / runs.csv: kết quả từng lượt và fingerprint của create-request.
- pairs.csv: mọi cặp lượt, so sánh create-request; 50 lượt tương ứng 1.225 cặp.
- summary.json: tổng số thành công, số cặp trùng IP probe/fingerprint/cả hai.
- audio_001.mp3 …: audio thành công, jobs/: manifest phục hồi và chunk.

Fingerprint ở đây là SHA256 của tập header được phép ghi và cấu hình TLS
impersonation. Không bao gồm cookie, CSRF, authorization; không phải JA3/JA4
đã đo trên mạng. Các header do thư viện tự sinh có thể không nằm trong tập này.
IP probe lấy từ ipify qua cùng session/tuyến trước các request; proxy luân phiên
có thể dùng IP khác theo kết nối hoặc đích. Không coi đây là bằng chứng IP mà
máy chủ TTS quan sát. `peer_ip` là đầu bên kia kết nối (có thể là proxy), không
phải IP thoát. IP download được đo riêng qua tuyến trực tiếp.

Các trường cố định như giọng, text, URL và payload được chủ ý giữ giống nhau.
Trùng fingerprint không có nghĩa toàn bộ request giống byte-for-byte. Lượt
không có create-request không được tính là cặp so sánh được; IP chưa đo được
không được tính là trùng. Các lượt tạo lỗi vẫn được ghi nhận trong báo cáo.

Báo cáo chứa IP và thông tin thiết bị, nên chỉ lưu cục bộ và không đưa vào release.
