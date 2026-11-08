# License, ví và quản trị key

Tài liệu hợp nhất hợp đồng license v1/v2, hạn ngày và hướng dẫn admin. Code chính: `license_manager.py`, `license_dialog.py`, `admin_key/admin_tool.py`. Không cần đọc bí mật để hiểu hoặc kiểm thử.

## Bộ khóa hiện có

- Public key: `assets/license/public_keys.json`; key ID công khai hiện có `9137cb1b957a2d7e349833eaea695193`.
- Private key mã hóa: `admin_key/secrets/signing_key.pem`.
- Mật khẩu phục hồi: `admin_key/RECOVERY_PASSWORD.txt`; **không đọc/hiển thị vào chat/log/context**.
- Backup ban đầu: `admin_key/backups/EverAI-admin-initial.zip`, được ghi nhận đã thử giải mã/ký/xác minh lúc tạo. Backup sau các lần cấp mới cần kiểm tra inventory thực tế.
- Issuer/issued/records/backups chỉ ở máy quản trị, không import/bundle vào app khách. Quyền cấp license khác mật khẩu mở Nhật ký quản trị. Khóa ký APK cũng là bộ khác.

Không tạo bộ khóa lần đầu khi đã có. Mất private key hoặc mật khẩu và không còn backup dùng được không thể tái tạo khóa cũ từ public key; không thay âm thầm bằng khóa mới hoặc thêm master key vào bản khách.

## Định dạng, mã máy và import

Ed25519 ký payload JSON canonical UTF-8. V1 có schema_version/product/key_id/license_id/machine_id/char_allowance/issued_at; V2 thêm expires_at. Parser strict từ chối field trùng/thừa, schema lạ, hạn mức không phải int dương/ngoài giới hạn, mã máy/chữ ký sai. Bool không được dùng thay int.

Windows: `EV1-` + SHA256(namespace sản phẩm + MachineGuid), không tự sinh định danh khi không đọc được. Cài lại Windows có thể đổi mã. Android dùng domain riêng + deviceId từ native/package/Android ID, vẫn định dạng EV1 để issuer cấp được. License/ví Windows không tự chuyển sang Android hay máy khác.

Key `EVKEY1-…` là Base64 URL-safe document đã ký, không nén, không mã hóa bí mật payload. Decoder cho whitespace nhưng giới hạn kích thước/độ dài. Import file bytes hoặc key str đi cùng verify và canonical document. Cùng license ID/cùng digest là no-op; cùng ID khác nội dung bị từ chối. Không cộng thêm do đổi cách nhập.

## Cách tính ký tự và job

- NFC, CRLF/CR→LF, strip đầu/cuối rồi chia; tính tổng Unicode code point của **chunk engine đưa vào render**, gồm dấu câu/khoảng trắng bên trong. Không dùng số byte hoặc số từ. Router provider mới có thể tạo im lặng cục bộ cho đoạn không có chữ/số nhưng job vẫn chốt phí chunk; không dùng bộ đếm ví làm hóa đơn API provider.
- EverAI SRT tính lời thoại đã trích; provider mới chia riêng mỗi cue, không tính timestamp. Source và EXE desktop-fixes-r2 dùng chung cách chia cue giữa GUI và engine, nên bộ đếm ký tự/chunk khớp pipeline; xem TTS_PROVIDER_MODULE. Các bản cũ trước r2 từng có sai lệch ước tính.
- Giữ trước đủ phần chưa thanh toán của cả bài trong transaction, thiếu thì chặn trước provider.
- Chunk lưu an toàn mới chuyển held→paid, commit cùng job/chunk là idempotent. Retry/resume/ghép lại cùng job không trừ lần hai; chunk paid mất/hỏng có thể tạo lại khi license còn cho request mới.
- Job mới/đổi nội dung hoặc cấu hình là chi phí mới. Mẫu EverAI offline/audio cũ/cache preview còn file không tính phí; render preview mới có job bền vững và tính phí. Cache bị xóa rồi tạo lại là lượt mới.

## Ví và crash recovery

`<data-root>/licensing/wallet.sqlite3` chứa meta, licenses, jobs, chunks, ledger. Chunks có pending/held/paid/reused; job/fingerprint/index và ledger giữ tính nhất quán. SQLite BEGIN IMMEDIATE, synchronous FULL, khóa OS theo job (msvcrt/fcntl) chống hai cửa sổ/process dùng cùng job. Ví gắn mã máy, quick_check/verify license khi đọc; lỗi DB không tự reset/mở khóa hạn mức.

Thứ tự worker: ghi chunk → commit_chunk/ledger → manifest. File và DB không cùng transaction; khi crash, recover_abandoned_jobs đối soát file/hash/ledger dưới job lock rồi trả held chưa tạo. Mở cửa sổ khác không thu hồi reservation của worker còn sống. Manifest cũ có chunk hợp lệ được nhận reused, không trừ hồi tố; phần thiếu vẫn phải reserve. Lỗi ghép không mất chunk đã trả phí.

Số dư API mặc định: total/used/held/available; `include_expiry=True` thêm expires_at/expired/remaining. Không tự đổi schema ví hoặc reset số dư khi thêm UI.

## Thời hạn v2

Admin nhập DD/MM/YYYY hoặc YYYY-MM-DD, cho chọn hôm nay, từ chối ngày đã qua/không hợp lệ. Ngày ghi là ngày cuối dùng theo UTC+07:00; expires_at là nửa đêm ngày kế tiếp, UTC epoch exclusive, phải >issued_at.

Hạn áp dụng toàn ví/app, lấy **xa nhất** trong các grant v2 hợp lệ; không chia bucket ký tự theo ngày riêng. Gia hạn cộng gói mới, giữ số dư cũ; key ngày sớm hơn/v1 không rút ngắn hoặc gỡ hạn v2. Ví chỉ có v1 chưa có hạn ngày. Key v2 mới đã hết hạn bị từ chối; key đã import rồi vẫn no-op khi nhập lại sau hết hạn.

Hết hạn: available=0, remaining giữ lại total-used-held. Engine kiểm tra trước reserve/chunk/retry; request đã gửi được hoàn thành/chốt phí, phần chưa tạo trả về. Audio đã có vẫn phát được. Resume chỉ ghép lại sau hết hạn khi không cần request mới và tất cả chunk paid còn file/hash hợp lệ. Cơ chế dựa đồng hồ local, chưa chống patch/lùi giờ/rollback toàn bộ dữ liệu.

## Cấp key

1. Từ module chạy `admin_key/MO_ADMIN.bat` hoặc `python admin_key/admin_tool.py`.
2. Chọn dùng mật khẩu phục hồi tại máy hoặc nhập mật khẩu riêng trong công cụ; không đưa mật khẩu vào lệnh/chat.
3. Dán mã máy khách, số ký tự, ngày hết hạn; ghi chú khách nếu cần.
4. Cấp license → Sao chép key EVKEY1 gửi khách. File `.everai-license` và records vẫn được lưu để quản lý.
5. Khách Cài đặt → Bản quyền, dán key → Kích hoạt key/Enter, hoặc Nhập từ file; kiểm tra số dư/hạn.
6. Backup admin sau đợt cấp, cất thêm ở thiết bị khác; cất mật khẩu tách ZIP khóa. Không gửi folder admin/backup/password cho khách.

CLI có issue/backup/check-backup/restore; xem --help. `issue --machine <EV1...> --chars 100000 --expires-on 31/12/2026 --print-key` xuất key thay path; mật khẩu prompt ẩn hoặc password-file, không tham số password trực tiếp. Công cụ hiện cấp v2; app v1 rất cũ không nhận v2, dùng bản khách hỗ trợ v2 hiện hành.

## Phục hồi

Admin mới: copy công cụ/public key, để secrets/issued/records trống → chọn Phục hồi admin/ZIP → nhập mật khẩu. Công cụ chỉ cho đường dẫn allowlist, từ chối ghi đè dữ liệu tồn tại, kiểm tra mở khóa/ký/xác minh khớp public key cũ trước ghi. Không tự thao tác phục hồi vào thư mục admin đang dùng khi chỉ kiểm thử.

Ví khách: dùng Sao lưu ví để xuất SQLite nhất quán. Phục hồi khi đã đóng mọi app, giữ bản lỗi để đối chiếu, đặt backup vào licensing/wallet.sqlite3 ở đúng data root/đúng máy. Không dựng lại used khi mất toàn bộ dữ liệu; chỉ backup hợp lệ hoặc cấp bù mới có ghi nhận. Offline không thu hồi tức thì license cũ trên máy khác. Test luôn dùng khóa/grant tạm, không dùng production key/ví hay thêm bypass.
