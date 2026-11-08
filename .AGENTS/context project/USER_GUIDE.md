# Cách dùng Voice Pro - chất lượng cao nhất

## Giao diện hiện hành — 27/10/2026

Sidebar theo thứ tự: **Studio giọng nói → Hàng đợi TXT → SRT → Proxy → Lịch
sử → Cài đặt**. Nhập TXT ở Studio/Hàng đợi; nhập hoặc mở project SRT tại trang
SRT riêng. SRT dùng số luồng riêng, mặc định 5; đổi số luồng Text/TXT không làm
đổi SRT. Dịch vụ có thể chọn chỉ gồm **Voice Pro - chất lượng cao nhất** và
**CapCut**. ElevenLabs, Edge và Saydi trong các phần lịch sử dưới đây không còn
trên giao diện hoặc runtime.

Trang Proxy chỉ hiển thị proxy custom người dùng quản lý. Tuyến nội bộ không
hiện trong header, bảng, trạng thái lưu lượng hoặc CSV. Máy Windows chưa từng
chạy app nhận trial 10.000 ký tự trong 48 giờ từ lần mở đầu; cài lại app trên
cùng máy không đặt lại trial. Có thể nhập license trả phí trong Cài đặt.

## SRT tăng tốc (26/10)

Bản srt-fast dùng số luồng đã chọn để tạo nhiều cue đồng thời, mỗi cue một
worker; tiến độ hiển thị số luồng đang chạy. Bắt đầu với 3 luồng, không tăng
vô hạn vì provider/proxy vẫn có giới hạn. EverAI phối hợp đổi IP an toàn
giữa các request và dùng HEAD lấy CSRF nhẹ hơn, fallback GET khi cần.

Muốn áp dụng cho bài đang dở: bấm Dừng và đợi worker dừng, đóng bản cũ rồi
mở bản mới, tiếp tục project đang có. Không nhập lại SRT thành project mới.
Raw/chunk hợp lệ được dùng lại, giữ cơ chế phí/resume. Tốc độ còn phụ thuộc
proxy và dịch vụ; benchmark offline không phải cam kết tốc độ TTS live.

## Giao diện PyQt6 mới (26/10)

Sidebar trái: Studio giọng nói, Hàng đợi TXT, Quản lý proxy, Lịch sử, Cài đặt.
Nút Chế độ sáng/Chế độ tối ở cuối sidebar đổi tức thì và ghi nhớ sau mở lại.
Cài mới dùng dark gần đen với gradient nhẹ. Cao độ/chất lượng trong Tùy chọn
nâng cao; luồng xử lý và thư mục xuất ở cột thiết lập, có cuộn dọc khi cửa sổ
nhỏ. Nút tạo/dừng/player luôn ở dưới; Ctrl+O nhập file, Ctrl+Enter tạo giọng,
Ctrl+Shift+O thêm TXT vào hàng đợi. Tab/phím mũi tên thao tác control và tua.
Ô lỗi text/token được đánh dấu và focus để sửa. Dữ liệu, ví và provider giữ
hợp đồng cũ. Những mô tả tab/Toplevel bên dưới là hướng dẫn bản Tk trước đó.

**Tìm Cao độ:** chọn dịch vụ EverAI → cuộn cột thiết lập bên phải xuống dưới
Thư mục xuất → bấm **Tùy chọn nâng cao +**. Cao độ và Chất lượng sẽ hiện ra;
bấm lại để thu gọn. Cao độ không bị bỏ khỏi app, chỉ chuyển vào nhóm này.
Với CapCut/Edge/ElevenLabs/Saydi, ô Cao độ bị khóa; giao diện hiện tại không
cho tùy chỉnh cao độ cho các provider đó.

Bản Windows/gói ZIP đúng và hash tại [CURRENT_STATE.md](CURRENT_STATE.md). Giải nén toàn bộ ZIP; EXE và `_internal` phải ở cùng thư mục. Không cần cài Python/Node/FFmpeg cho gói này.

## Tạo giọng

### SRT theo timeline (26/10)

Studio → Nhập file → chọn SRT UTF-8. App mở chế độ **SRT theo timeline** cho
cả EverAI, CapCut, Edge, ElevenLabs và Saydi. Bản nháp văn bản được giữ riêng.
Chọn một giọng/cấu hình chung, chính sách căn và WAV/MP3 rồi bấm **Tạo / xuất
SRT**. App đối soát phần đã có và hỏi số ký tự còn cần tạo trước khi chạy TTS.

- **Tự động · luôn xuất** mặc định tăng tốc tối đa 1,35×. App bỏ khoảng lặng
  thừa, mượn tối đa 250 ms ở khe nghỉ trước/sau; nếu câu vẫn dài thì giữ đủ
  lời, đẩy các câu sau nối tiếp và tận dụng khe nghỉ tiếp theo để bắt kịp.
  **Nén mạnh** cho phép tối đa 4×; cả hai chế độ đều không chặn xuất vì câu dài.
- Cue ngắn giữ tốc độ; bộ ghép thêm im lặng tới mốc kế tiếp. Output dài bằng
  mốc SRT cuối hoặc phần lời cuối thực tế, lấy giá trị lớn hơn. Cue chồng nhau
  trong file vẫn phải sửa trước khi nhập; sửa cue trong
  project cũng kiểm tra chồng lấn trước khi lưu. Mỗi cue có thể gồm nhiều chunk.
- Cuộn cột nội dung bên trái để tới bảng cue và phần sửa/nghe. Chọn cue, sửa
  lời thoại hoặc Từ/Đến theo HH:MM:SS,mmm rồi Lưu cue. Bản đang sửa chưa lưu
  cũng được giữ trong bản nháp riêng qua lần mở app; khi tạo, app kiểm tra và
  áp dụng tất cả cue đang sửa cùng một lần.
- Nghe gốc/Nghe đã căn dùng audio cue có sẵn. Tạo lại cue hỏi phí mới và chỉ
  tạo cue được chọn, không thay ngay file timeline đã xuất. Bấm Tạo / xuất SRT
  sau đó để ghép timeline mới.
- Đổi timestamp/chính sách/định dạng xuất dùng lại audio gốc, không gọi TTS.
  Sửa lời thoại/giọng/model/tốc độ TTS cần tạo giọng mới ở các cue thay đổi.
  Nếu audio gốc bị xóa/hỏng sau khi chunk đã dọn, cần Tạo lại cue có phí mới.
- **WAV mono 48 kHz/16-bit là bản chuẩn timeline** để dựng video. MP3 được mã
  hóa từ PCM hoàn chỉnh; một số player có thể xử lý encoder padding khác nhau.
  Đồng bộ theo cue, không phải timestamp từng từ hoặc khẩu hình. Khi tổng lời
  không thể nằm trong các cửa sổ dù đã nén, file kết quả có thể dài hơn SRT;
  đây là chủ ý để không mất lời và vẫn luôn có file xuất.
- Project nằm ở data root/srt-projects/<id>/project.json, kèm snapshot SRT,
  job con và audio gốc. Mở project chọn project.json để tiếp tục. Dừng/đóng app
  chờ worker kết thúc và giữ phần đã làm; export chưa hoàn tất không được ghi
  nhận là kết quả thành công. Giới hạn: 10 MiB, 10.000 cue, timeline 6 giờ.

**Ngọc Huyền của Saydi AI:** chọn dịch vụ `saydi`, dán Bearer token của phiên
Saydi hợp lệ vào ô được che ký tự, rồi chọn Ngọc Huyền. Token chỉ giữ khi app
đang mở; không gửi token qua chat/hỗ trợ. Cần nhập lại sau mở app hoặc hết
phiên. App không tự đăng nhập/giải xác thực Saydi. Provider này kết nối trực
tiếp, chia tối đa 350 ký tự/đoạn; TXT queue, SRT, tốc độ, nghe mẫu có phí và
tiếp tục job hoạt động qua engine hiện có. Hạn mức Saydi là hạn mức riêng của
tài khoản/phiên bạn dùng, song song với ví Studio.

CapCut bản sửa 25/10 dùng task API và 122 giọng/resource từ catalog gốc
VIRAL_REUP, kết nối trực tiếp, không cần cấu hình proxy. Khi đang dùng lựa chọn
CapCut cũ, chọn lại giọng trong danh sách mới; job cũ không được tự đổi giọng
để tiếp tục. EverAI/Edge/ElevenLabs giữ cách kết nối riêng đã mô tả.

1. Trong tab Tạo giọng nói, chọn dịch vụ EverAI/CapCut/Edge/ElevenLabs, ngôn ngữ và giọng phù hợp. ElevenLabs nhập API key của mình, chọn model; quyền dùng voice phụ thuộc tài khoản.
2. Dán text hoặc Nạp TXT/SRT UTF-8. Khi đã có nội dung, hộp hỏi cho thay/nối/hủy. Ctrl+O nạp file, Ctrl+Enter bắt đầu; Xóa có thể Ctrl+Z.
3. Chọn tốc độ và nơi lưu. EverAI có thêm cao độ/bitrate/model; provider mới khóa cao độ/bitrate, chuẩn hóa MP3 mono/44,1 kHz/128 kbps.
4. Chọn Số luồng 1–8 cạnh Tạo/Dừng, mặc định 3. Tạo/Lưu nhớ lựa chọn; Hủy trong Cài đặt phục hồi giá trị đã lưu.
5. Tạo giọng nói; chờ ghép/lưu. Nghe bằng player, pause/tua/resume hoặc Mở vị trí file. Tên tự sinh, xuất trùng tên giữ cả hai file.

SRT desktop hiện hành: dùng quy trình **SRT theo timeline** ở trên. Tk/bản cũ
desktop-fixes-r2 mới có khác biệt EverAI đọc liên tục và provider mới tự nhận
timestamp trong editor. Các job văn bản/TXT cũ vẫn giữ hợp đồng của bản tạo job.

## Hàng đợi nhiều TXT (source 24/10/2026)

Nạp TXT / Thêm TXT / Ctrl+O cho chọn một hoặc nhiều TXT UTF-8. Bảng duyệt sắp tên tự nhiên (1, 2, 10), cho Lên/Xuống, thêm file, sắp lại theo tên, bỏ file và Hủy. File trống/lỗi đọc phải bỏ trước khi Đưa vào hàng đợi. Danh sách chỉ bắt đầu khi bấm Tạo giọng; có thể đổi thứ tự/bỏ file trước khi chạy.

Chọn Nhập văn bản hoặc Hàng đợi TXT để quyết định nút Tạo chạy nội dung nào. Mỗi TXT xuất một MP3 theo tên file; trùng tên giữ cả hai. File chạy tuần tự, số luồng vẫn áp dụng cho chunk bên trong mỗi file. Cấu hình giọng/model/thông số được chốt cho lượt hàng đợi; khi tiếp tục phần dở dùng cấu hình đã chốt. Thư mục xuất có thể đổi khi dừng.

Dừng hoặc lỗi tạm ngừng danh sách. Bấm Tạo/Tiếp tục để thử lại job dở, hoặc chọn dòng và Bỏ qua lỗi rồi tiếp tục. Danh sách, bản chụp text và job được lưu trong txt_queue.json tại data root; mở lại app chờ người dùng tiếp tục, không tự chạy. Sửa TXT gốc sau khi duyệt không đổi nội dung đã nhập. Thêm file khi danh sách cũ đã hoàn tất sẽ chốt cấu hình hiện tại cho lượt mới.

Tính năng này có trong EXE Windows ngày 24/10/2026 (xem CURRENT_STATE.md); APK chưa cập nhật.

## Cơ chế Anti-Detect theo phân đoạn & Bản sao Voice Pro Studio (25/10/2026)

- **Anti-Detect từng Chunk:** Tùy chọn **"Thay đổi hồ sơ thiết bị"** (mặc định bật) sẽ tự động tạo một Fingerprint mới (Chrome 130-132, Client Hints, User-Agent) và kết nối qua một IP mới cho từng phân đoạn câu. Mỗi chunk fetch một CSRF token độc lập, triệt tiêu nguy cơ bị WAF chặn hoặc liên kết các phân đoạn.
- **Bản sao White-Label "Voice Pro Studio":** Bản build `Voice_Pro.exe` ẩn toàn bộ thông tin/tên gọi "Ever AI", hiển thị giao diện "Voice Pro Studio" với các dịch vụ "Voice Pro", "CapCut", "ElevenLabs", "Edge TTS" và mô hình "VoicePro-v1.6/v1.5".

## Nghe mẫu, kết quả và dừng

EverAI Nghe mẫu phát mẫu gốc offline, không mạng/không trừ ký tự, không phản ánh tham số vừa chỉnh. Bộ 49 mẫu phải đi cùng ứng dụng. Provider mới Nghe mẫu tạo audio thật và dùng hạn mức nếu chưa có cache; cache còn sẵn phát lại miễn phí. Nút Theo tùy chỉnh cũ đã bỏ khỏi UI.

Nghe mẫu không đổi kết quả chính; Nghe kết quả đưa về file vừa tạo. Lịch sử giữ 10 kết quả; bỏ mục lịch sử không xóa MP3. App nhớ bản nháp/thông số/nơi lưu qua lần mở.

Dừng giữ các chunk đã tạo và chờ worker kết thúc. Tiếp tục với cùng text/giọng/model/tham số chỉ tạo phần thiếu/hỏng; đổi nội dung/cấu hình âm thanh thì bắt đầu bài mới. Có thể đổi nơi lưu để tiếp tục ghép. Tiến độ bắt đầu từ phần đã có; 100% sau lưu file, không theo thứ tự worker hoàn tất.

Bản desktop-fixes-r2 ngắt được tiến trình FFmpeg khi Dừng; remux/timeline có thời hạn 180 giây mỗi lệnh, chuẩn hóa provider 120 giây. Các chunk đã tính phí được giữ để ghép lại. Khi phục hồi job khác nội dung/cấu hình hiện tại, app hỏi trước khi nạp lại bản chụp job. Khi hàng đợi đã xong, nút Tiếp tục có thể chuyển về Nhập văn bản để phục hồi job dở phù hợp; job đã Bỏ qua không tiếp tục hiện trong nhắc phục hồi.

## Bản quyền và số dư

Mở Cài đặt → Bản quyền hoặc bấm số dư header, sao chép mã máy gửi admin. Dán toàn bộ `EVKEY1-…` → Kích hoạt key/Enter; file cũ dùng Nhập từ file. Cùng license qua file/key cộng một lần. Sao lưu ví để giữ dữ liệu khôi phục.

App giữ trước đủ ký tự cho bài, chốt từng đoạn thành công, trả phần chưa tạo sau lỗi/dừng. Retry/resume cùng job không trừ lặp; bài mới có phí mới. Hạn tính hết ngày Việt Nam; hết hạn giữ số dư để gia hạn. Gia hạn cộng gói và giữ số dư cũ, lấy ngày xa nhất. Chưa kích hoạt/hết số dư/hết hạn vẫn sửa bài, nghe mẫu offline và phát audio đã có.

## Bố cục, proxy và dữ liệu

Ba tab Tạo giọng nói/Quản lý proxy/Lịch sử; thanh Tạo/Dừng/player luôn ở đáy. Cài đặt góc phải có kết nối/session, dữ liệu, hỗ trợ và bản quyền. Đóng Cài đặt giữ bản đang sửa; Hủy hoàn nguyên; Lưu áp dụng. Điều khiển khóa trong lúc chạy.

Proxy: thêm → lưu → Kích hoạt → Lưu cài đặt. Proxy builtin mặc định luôn được dùng khi chưa có proxy custom active; proxy builtin không hiện trong danh sách. Proxy mới/import chưa tự active, chỉ proxy custom đã kích hoạt mới thay mặc định. EverAI mặc định tải MP3 trực tiếp; đây là tùy chọn đường tải audio riêng, không tắt proxy API. Proxy builtin là SOCKS cho EverAI; provider mới chỉ hỗ trợ HTTP/HTTPS qua adapter, nên cần custom HTTP/HTTPS active và chưa live test đầy đủ. Bảng byte/request không thay hóa đơn proxy và chưa đo provider mới.

Dữ liệu mặc định `%LOCALAPPDATA%/EverAIStudio/`; portable.flag cạnh EXE chọn EverAIStudio-data cạnh đó. Session EverAI chỉ RAM; API key ElevenLabs lưu settings local, draft/job lưu nội dung phục hồi. Không gửi nguyên folder dữ liệu cho người khác. Log kỹ thuật cần mật khẩu quản trị riêng; Sao chép hỗ trợ dùng bản tóm tắt. Chi tiết vận hành: [OPERATIONAL_GUIDE.md](OPERATIONAL_GUIDE.md). Admin: [ADMIN_KEY_GUIDE.md](ADMIN_KEY_GUIDE.md).
