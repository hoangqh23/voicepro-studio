# Kế hoạch SRT → audio theo timeline

Ngày: 26/10/2026, Asia/Saigon.
Trạng thái: đã tích hợp vào source desktop PyQt6 theo yêu cầu người dùng.
Artifact/test snapshot cuối xem CURRENT_STATE.md; phần mô tả source cũ ở mục 2
là bằng chứng trước triển khai. Fit v2 áp dụng auto1.35/force4 và luôn xuất.

## 1. Kết luận thiết kế

Thêm chế độ/project SRT riêng trong desktop PyQt6. Giữ engine chunk hiện có
làm tầng tạo giọng và kế toán. Project quản lý cue, timestamp, audio cue,
chính sách fit và lần xuất; không gộp các cue thành một request TTS.

Quan hệ:

    SRT project
      → cue có ID ổn định + start_ms/end_ms
        → generation bất biến
          → job engine có 1 hoặc nhiều chunk
        → audio gốc đã ghép của cue
        → bản PCM đã fit
      → audio timeline hoàn chỉnh

Phạm vi bản đầu: một file SRT/project, một giọng/cấu hình TTS chung, tất cả
provider desktop hiện có, bảng cue, nghe cue, sửa text/timestamp, tạo lại cue,
dừng/resume, fit và xuất WAV/MP3. Chưa có đa nhân vật, video, waveform editor,
batch nhiều SRT hoặc đồng bộ từng từ/khẩu hình. TXT queue vẫn là luồng riêng.

## 2. Bằng chứng từ source và giới hạn hiện tại

- `qt_gui.py:load_text_file`: EverAI lấy lời thoại SRT thành văn bản liên tục;
  provider khác giữ SRT. Đây là khác biệt cần loại bỏ trong chế độ SRT mới.
- `tts/srt.py:split_srt_chunks`: đã chia riêng từng cue, lưu indices các chunk.
  Không phải hiện nay mọi chunk đều cắt xuyên cue; vấn đề chính là thiếu
  project/cue management và EverAI chưa dùng đường timeline.
- `tts/srt.py:assemble_timeline`: concat chunk theo cue, đo bằng stderr FFmpeg,
  tăng tốc không có ngưỡng, tạo stem kéo dài từ thời điểm 0 rồi trộn nhiều lần.
  Cách này tốn I/O với file dài và chưa có báo cáo cue vượt giới hạn.
- `engine.py:process`: fingerprint gắn text/config/chunk_version/max_chunk_chars;
  job ID gắn đường dẫn job. `LicenseManager` kiểm tra cùng fingerprint và cùng
  danh sách chunk/path. Không thể đổi fingerprint trong cùng job để tái sử dụng.
- `engine.py:_execute_job`: sau publish thành công, chunk bị xóa khi có job_dir.
  Vì vậy không thể cam kết sửa/fit lại dựa trên chunk cũ đã bị dọn.
- Kết quả completed hiện chỉ kiểm tra file tồn tại trước trả lại; tầng SRT mới
  cần hash/decode validation audio cue riêng khi tái sử dụng.
- `audio_filename` và target tạm của core đang cố định MP3. WAV timeline phải
  được xuất ở tầng project, không chỉ đổi đuôi file MP3.
- Chỉ mục module không chứa test repo gốc; “no tests found” của graph không
  phản ánh toàn bộ bộ test hiện có.

## 3. Vì sao chọn job con theo cue

Ba cách đã cân nhắc:

1. Chia toàn bộ lời thoại thành chunk chung: mất ranh giới cue trong audio,
   cần forced alignment/word timestamp để tách lại; loại khỏi bản đầu.
2. Một job phẳng chứa mọi chunk, ánh xạ ngược cue: hiệu quả cho lần render đầu,
   nhưng sửa một cue/timestamp hoặc giữ raw sau completed đòi thay fingerprint,
   cleanup và ledger core. Có thể phát triển sau nếu profiling chứng minh cần.
3. Project SRT chứa job con theo cue: đề xuất. Dùng nguyên reservation,
   commit/recovery chunk của engine, giữ output cue bền vững làm nguồn fit.
   Đổi timestamp không chạm job tạo giọng; sửa text tạo generation/job mới.

Chi phí của cách 3: nhiều manifest/giao dịch SQLite/request nhỏ. Chấp nhận để
cô lập sửa/resume và giảm phạm vi thay đổi engine/license. Không gộp cue ngắn
chung request chỉ để giảm số request khi provider không có alignment chuẩn.

## 4. Hợp đồng dữ liệu và nhập file

### 4.1 Project/cue

- Lưu snapshot SRT gốc; file gốc đổi/di chuyển không làm project đã lưu thay đổi.
- `cue_id`: UUID riêng, ổn định trong project; số thứ tự SRT chỉ để hiển thị,
  không dùng làm primary key vì có thể trùng/đổi.
- Timestamp dùng số nguyên millisecond, không cộng dồn float.
- Giữ text hiển thị và text gửi TTS đã chuẩn hóa; đếm phí trên đúng chunk text
  mà engine gửi, không đếm số thứ tự/timestamp/markup SRT.
- Chốt chunk limit theo provider trước khi ước lượng: Saydi tối đa 350, provider
  còn lại theo splitter/config hiện hành. Dùng cùng helper cho UI và engine.
- Trong mỗi cue chia theo câu/cụm từ rồi giới hạn ký tự; tuyệt đối không tạo
  chunk chứa text của hai cue. Cue 650 ký tự có thể tạo nhiều chunk; concat
  tất cả chunk của cue rồi mới fit. Không tự phân timestamp cho từng chunk.
- Trước TTS chỉ đưa ra cảnh báo ước lượng độ dài, không hứa số đo chính xác.

### 4.2 Validation trước tính phí

- Hỗ trợ UTF-8/UTF-8 BOM, LF/CRLF; báo rõ encoding lỗi, không đoán bừa.
- Chấp nhận dấu phẩy/dấu chấm millisecond theo parser hiện có.
- Kiểm tra start >= 0, end > start, phút/giây hợp lệ, cue có lời đọc,
  timestamp đủ chính xác; báo file/cue/dòng lỗi.
- Số cue trùng hoặc không liên tiếp không làm thay đổi lời đọc; báo cảnh báo.
- Giữ timestamp, hiển thị theo start rồi vị trí nguồn; không âm thầm dời mốc.
- Bản đầu CHẶN overlap trước TTS. Quét theo start với max_end của mọi cue trước
  để phát hiện cả cue bị chứa trong cue dài; start == max_end là tiếp giáp hợp lệ.
  Người dùng sửa timestamp hoặc bỏ cue rõ ràng. Chưa có tùy chọn mix chồng tiếng.
- Cue trống, chỉ markup hoặc không có lời đọc: đánh dấu cần sửa/bỏ; không âm
  thầm bỏ hoặc thu phí cho cue im lặng. Bỏ cue là thao tác project có chủ ý.
- Không tự bỏ nội dung trong ngoặc như [cười]; chỉ bỏ markup hiển thị đã xác
  định. Giữ xuống dòng nguồn, dùng normalization nhất quán cho text gửi TTS.
- Import SRT thay project có xác nhận khi đang có bản nháp; không có “nối thêm”
  SRT bằng cách nối chuỗi vì timestamp hai file thường bắt đầu lại từ 0.
- Giới hạn đề xuất bản đầu: 10 MiB SRT, 10.000 cue, timeline 6 giờ. Báo rõ khi
  vượt; kiểm tra dung lượng đĩa theo sample count, không chỉ kích thước SRT.

## 5. Identity, file và resume

    <data>/srt-projects/<project_id>/
      source.srt
      project.json
      cues/<cue_id>/generations/<generation_id>/
        job/manifest.json
        raw.mp3
        raw-metadata.json
      fit/<fit_key>/cue.wav
      exports/<export_id>/report.json

- Child job dùng đường dẫn ổn định trong một generation. Output raw.mp3 nằm
  ngoài thư mục chunk job; sau core cleanup vẫn còn file raw đã ghép.
- `generation_id` mới được cấp khi tạo giọng lần đầu, sửa text/giọng/model/
  speed/pitch hoặc chủ động “Tạo lại giọng”. Cùng text ở hai cue vẫn là hai
  job tính phí riêng; bản đầu không có cache cross-job/cross-project.
- Cấu hình TTS chốt theo generation; token/session không vào manifest/hash.
  Proxy/concurrency không làm thay voice identity, theo hợp đồng core.
- Đổi timestamp, fit policy, ngưỡng tăng tốc hoặc WAV/MP3 không tạo generation
  mới. Giữ raw và chỉ vô hiệu hóa fit/export liên quan.
- `fit_key` gồm raw hash + số mẫu cửa sổ + fit policy/max_tempo + PCM format +
  fit algorithm version. Start timestamp không cần thuộc fit_key khi cửa sổ
  dài như cũ; placement thuộc export identity.
- Export identity gồm danh sách cue có thứ tự, start/end, generation/raw hash,
  fit key, format và algorithm version. Không dùng hash của đường dẫn file.
- Project chỉ ghi tham chiếu generation mới sau khi tạo cấu trúc và ghi JSON
  atomic thành công. Không sửa manifest child cũ để hợp thức hóa nội dung mới.
- Dùng lock toàn project khi chạy/chỉnh revision/export; cùng project không
  được hai tiến trình ghi đồng thời. Mọi JSON/hash/path từ đĩa phải validate
  schema và containment; không đi theo path thoát project.
- Sau crash giữa child publish và parent metadata: reconcile manifest/result
  đúng generation, decode + hash rồi cập nhật parent; không gọi TTS lại.
- Raw mất/hỏng khi child đã completed và chunk đã dọn: báo asset mất/hỏng.
  Muốn render lại phải tạo generation mới và hiển thị phí. Không thể khôi phục
  miễn phí từ ledger khi file âm thanh đã không còn.
- Resume child chưa xong: dùng đúng job/fingerprint/paid chunks hiện có.
- Cleanup chỉ xóa .part/temp orphan đã xác định; raw/generation còn tham chiếu
  được giữ sau export. Dọn dữ liệu do người dùng chủ động, có báo ảnh hưởng.

## 6. Lập lịch tạo giọng, tính phí và hủy

1. Parse/validate toàn bộ SRT; chốt project revision và audio config.
2. Lập kế hoạch cue/chunk và đối soát asset/job đã có; tính số ký tự còn cần
   gửi. Không tính lại cue raw hợp lệ hoặc chunk đã paid trong cùng job.
3. Kiểm tra license/số dư trước render. Child engine vẫn reserve/commit thật.
   Không thêm một khoản reserve cha chồng lên reserve con.
4. Để nguyên cơ chế accounting core. Preflight toàn project là snapshot,
   không phải reservation toàn project: nếu app khác dùng ví đồng thời thì
   child sau có thể thiếu hạn mức. Dừng rõ ràng và giữ kết quả đã tạo.
5. Mỗi cue gọi engine.process với TEXT thuần và output raw.mp3; các provider
   giữ cơ chế tốc độ/chuẩn hóa hiện hành, không áp tốc độ người dùng lần hai.
6. Khi raw sẵn sàng, coordinator ghi hash/metadata và phát sự kiện theo cue_id.
7. Render xong toàn bộ lời đọc mới bước sang fit/export. Cue lỗi được giữ trạng
   thái, mặc định dừng phát thêm công việc; không tự bỏ cue để xuất “hoàn tất”.

Concurrency phải có một cấp sở hữu ngân sách TTS:

- Bản tăng tốc 26/10: mọi provider tối đa K cue song song, mỗi engine con
  concurrency=1; K là số luồng UI. Không nhân thành K×K request.
- EverAI có RouteCoordinator dùng chung trong một SrtRunner: request lease
  chạy đồng thời; khi đổi IP, trả lease của caller, chặn request mới, chờ
  request còn sống hoàn tất rồi đổi. Epoch gộp các lỗi đồng thời thành một
  lần đổi tuyến; cancel không bỏ lại lease. Core giữ retry/profile/CSRF riêng.
- Edge/ElevenLabs không dùng bộ đổi tuyến của EverAI; scheduler proxy của
  phiên SRT bị đình chỉ theo hợp đồng UI hiện có. Không đổi proxy ngoài runner.
- CSRF HEAD chỉ bật trên child EverAI của SRT, cùng session/proxy/profile;
  không có token/lỗi HEAD thì GET như cũ. Text/TXT mặc định vẫn GET.
- Bỏ lần decode chỉ để đo raw sau remux; ghi frames=null, đo PCM khi fit,
  cập nhật lại metadata. Checksum raw vẫn kiểm trước resume/export.
- Scheduling provider/route là policy nội bộ, UI giải thích số luồng là tối đa,
  không cam kết mọi cue ngắn đều chạy đồng thời. Không viết lại proxy engine.
- Chỉ submit cửa sổ hữu hạn công việc đang chờ, không hàng chục nghìn future.
- Fit FFmpeg chạy tuần tự ở bản đầu, không cạnh tranh K tiến trình TTS/FFmpeg.

Hủy: project cancel ngừng submit, gọi cancel mọi engine đang hoạt động và FFmpeg,
chờ worker kết thúc rồi mới unlock UI/project. Mỗi engine được tạo mới khi
resume; không tái dùng engine mang cancel_event đã set. Chốt phí các chunk
thành công vẫn theo core. Tổng hợp traffic mọi child, kể cả child lỗi, bằng
delta/ID riêng để không cộng lặp khi resume.

Trạng thái cue: pending → rendering → raw_ready → fitting → ready; nhánh error
hoặc cancelled dành cho lỗi tạo/đọc dữ liệu thật. Câu dài không còn chuyển
needs_attention và không chặn project. Trạng thái project phân biệt rendering,
fitting, exporting, completed, interrupted. Completed child không có nghĩa
completed project.

## 7. Thuật toán audio theo sample

### 7.1 Chuẩn làm việc

- PCM mono 48.000 Hz/16-bit cho fit và timeline. Raw nguồn có thể là MP3 theo
  core; decode về PCM trước đo, không đọc Duration từ stderr làm số đo chuẩn.
- SRT millisecond ánh xạ chính xác: start_frame = start_ms * 48,
  end_frame = end_ms * 48; W = end_frame - start_frame.
- Duration D = số frame PCM thực tế, có tính đến codec delay/padding mà decoder
  hỗ trợ. Hash/version pipeline ghi trong metadata.
- Timestamp quy định mốc mong muốn của cue, không xác định chính xác âm vị đầu.
  Fit v2 dò năng lượng theo block 10 ms với ngưỡng thích nghi thấp, giữ đệm
  60 ms quanh biên speech và tối đa 180 ms cho khoảng nghỉ nội bộ. Raw bất biến;
  chỉ PCM cache được rút silence nên có thể fit/xuất lại không gọi TTS.

### 7.2 Fit từng cue

Mặc định: auto_fit, max_tempo=1.35. Tùy chọn force_fit tăng tốc tối đa 4.0×.
Cả hai chính sách đều luôn xuất và không làm chậm cue ngắn.

    trim silence bảo thủ -> D
    lead = min(250 ms, khe trống trước - safety)
    tail = min(250 ms, khe trống sau - safety)
    earliest = max(cursor, nominal_start - lead)
    W = nominal_end + tail - earliest
    nếu D > W: atempo min(D/W, max_tempo), decode và đo lại
    nếu D vẫn > W: giữ nguyên đủ phần lời đã nén
    đặt gần nominal_start nhất nhưng không trước earliest/không đè cursor
    cursor = placed_start + actual_frames
    cue sau lặp lại với cursor mới; khe nghỉ sau tự giúp bắt kịp

- FFmpeg atempo không bảo đảm output dài chính xác D/ratio; phải đo lại.
  Có thể chừa headroom thời gian nhỏ để giảm lặp, nhưng ratio thực tế không
  được vượt max_tempo đã chọn. Trường hợp sát ngưỡng cần kiểm tra riêng.
- Chain atempo mỗi bước <=2 khi cần; ghi ratio thực tế trong báo cáo.
- Mỗi lượt fit bắt đầu từ PCM raw, không tăng tốc lại output đã tăng tốc.
- Không dùng atrim để cắt phần lời đọc dư và giả vờ fit thành công; chỉ bỏ vùng
  được phân loại silence và luôn giữ raw nguồn.
- Không tự kéo dài lời ngắn/crossfade/chồng speech. Có mượn khe nghỉ và dời
  speech về sau khi bắt buộc; metadata ghi nominal/placed/shift/overflow frame.
- Lỗi atempo thì dùng PCM đã trim ở tốc độ 1× và vẫn xuất. Sửa timestamp/policy
  chỉ fit lại; sửa text cần TTS mới và hiển thị phí.
- Khi đổi tốc độ TTS toàn project, cần generation mới ở các cue dùng cấu hình
  đó. Fit ratio là bước căn bổ sung, khác tốc độ giọng mà người dùng đã chọn.

### 7.3 Ghép tuyến tính, không tạo full-length stem cho mỗi cue

Vì input chặn overlap và placement bảo đảm speech tuần tự, ghép PCM một lượt:

    cursor = 0
    for cue in order_by_start:
        start = max(cursor, cue.placed_start_frame)
        stream_zeros(start - cursor)
        stream_exactly(cue.fitted_pcm, cue.speech_frames)
        cursor = start + cue.speech_frames
    stream_zeros(max(max_srt_end, cursor) - cursor)

- Ghi theo buffer cố định; không tạo mảng zeros bằng chiều dài video trong RAM.
- Fit output chỉ chứa phần speech đã trim/nén; khoảng trống timeline được ghi
  bằng zeros khi ghép. Kiểm tra hash/frame từng cache trước và sau ghép.
- Độ phức tạp placement O(tổng frame + số cue), RAM theo kích thước buffer;
  không trộn lại timeline từ giây 0 theo từng batch.
- Ghi timeline WAV tạm bên cạnh output để atomic publish. Với PCM dự kiến >=
  giới hạn WAV RIFF, chặn trước hoặc dùng RF64 đã nghiệm thu; bản đầu giới hạn
  6 giờ mono48k16 ở dưới giới hạn RIFF, vẫn tính size thực trước khi chạy.
- WAV là bản chuẩn timeline. MP3 encode một lần từ PCM hoàn chỉnh. Encoder
  delay/padding và player có thể gây khác biệt; báo cáo nêu WAV sample count
  chính xác, MP3 kiểm decoded-duration tolerance và gapless metadata trên
  player đã test. Không hứa mọi player đọc MP3 giống nhau từng sample.
- Bản đầu xuất từng format theo lựa chọn, mỗi file publish atomic; manifest
  đánh dấu completed sau khi file và report cần thiết đã lưu an toàn.

## 8. UI PyQt6 và phạm vi tích hợp

- Studio có lựa chọn “Văn bản” / “SRT theo timeline”; import .srt đi vào SRT.
  Có thao tác riêng “Lấy lời thoại thành văn bản” nếu muốn đọc liên tục.
- Trong SRT mode thay editor chính bằng bảng model/view để xử lý hàng nghìn
  cue; cột STT/start/end/text/thời lượng raw/tăng tốc/trạng thái.
- Panel bên phải giữ provider/voice/model; thêm fit policy, max tempo, format.
- Chi tiết cue cho sửa text/timestamp khi idle, nghe raw/nghe đã fit và tạo lại.
  Không cho sửa revision đang chạy; khóa cả setting ảnh hưởng audio.
- Trước TTS hiển thị cue/chunk/ký tự cần tạo; tạo lại hiển thị phí. Fit và
  xuất lại từ raw hợp lệ ghi rõ không gọi TTS.
- Lưu srt draft/project riêng; chuyển mode không ghi đè draft văn bản. Đổi
  provider không biến SRT thành plain text và không tự tái render toàn bộ.
- Khi mở project phục hồi đúng revision, generation, trạng thái lỗi/interrupted.
  Chọn file mới không tự nhận là project cũ chỉ vì tên file giống nhau.
- Coordinator phát queue events, QTimer cập nhật main thread. Tiến độ hiển thị
  rõ “Tạo giọng x/y cue”, “Căn thời gian x/y”, “Xuất file”; progress child không
  trực tiếp ghi progress tổng gây reset về 0 hoặc báo 100% quá sớm.
- Project SRT có recovery index riêng; không để child job lẫn trong recovery
  TXT/text cũ. History ghi project_id/revision/format và đường dẫn kết quả.
- Player Windows MCI phải được kiểm với WAV48k và MP3; lỗi player có mở ngoài
  theo hành vi hiện có, không làm export thành lỗi giả.

## 9. Phân chia module đề xuất

Giữ tất cả trong src/everai; không import src/core/src/utils.

| File | Trách nhiệm |
| --- | --- |
| `srt_studio/model.py` | Project/Cue/Generation schema, validation, identity/version |
| `srt_studio/parser.py` | Đọc SRT có vị trí lỗi, normalized text, kiểm overlap |
| `srt_studio/store.py` | Snapshot/project lock/atomic metadata/hash/reconcile |
| `srt_studio/runner.py` | Lập lịch cue, engine factory, cancel, progress/traffic |
| `srt_studio/audio.py` | Decode/count PCM, trim/fit thích nghi, ghép stream, encode |
| `srt_studio/qt_panel.py` | Bảng cue model/view và panel sửa/nghe, Qt main thread |
| `qt_gui.py` | Mode switch, engine factory hiện có, player/history/recovery |
| `tts/engine.py` | Explicit input_mode cho job cue thuần text, giữ legacy default |
| `utils.py` | Helper chung file/audio cần dùng lại, format-aware filename nếu cần |
| `tools/verify_desktop_release.py` | Kiểm module/pipeline SRT có trong bundle |

Tận dụng parser/split helper hiện có nếu giữ được hợp đồng; wrapper/adapter
cho legacy API thay vì thay đổi trả về dict âm thầm. Không dùng chuỗi '-->'
để quyết định mode của job cue mới; text thường cũng có thể chứa chuỗi này.
Không bắt buộc refactor code text/TXT hay di trú job cũ để làm feature này.

## 10. Kế hoạch triển khai theo mốc

### Mốc 1 — Model, parser, project store

- Thêm schema version, cue ID, timestamp integer, diagnostics/overlap scan.
- Snapshot/import và lock/atomic save/reconcile skeleton, srt draft riêng.
- Xác minh cue dài chia nhiều chunk và bộ đếm giống engine từng provider.
- Hoàn thành khi invalid SRT bị chặn trước engine và reload project không đổi ID.

### Mốc 2 — Render cue qua engine hiện có

- Thêm explicit plain-text mode có tương thích ngược cho MultiProvider.
- Engine factory nhận config/credentials RAM từ Qt; per-cue generations và
  output raw ổn định, mapping child job không lẫn recovery legacy.
- Điều phối concurrency/cancel; tổng hợp progress/traffic; preflight chi phí.
- Hoàn thành khi resume job bị ngắt chỉ gửi chunk còn thiếu và không tính phí
  lặp; cue completed còn raw hợp lệ được tái dùng; token không vào JSON.

### Mốc 3 — Fit và timeline offline

- Đo bằng decoded PCM, auto/force policy và frame validation sau atempo.
- Cache fit theo hash; ghép WAV streaming + MP3 encode/publish + report.
- Cue không fit trong cửa sổ được giữ đủ và đẩy tiếp; cancel/timeout/disk-full
  vẫn giữ trạng thái resume.
- Hoàn thành khi timestamp/tổng frame đúng, không cắt lời, không gọi TTS khi
  chỉ đổi timestamp/policy/format, và file dài không dùng RAM theo timeline.

### Mốc 4 — UI SRT riêng

- Mode switch, import, bảng cue, edit/preview/retry, policy và export format.
- State/errors, project restore, progress theo phase, history/player.
- Hoàn thành khi mọi cue có raw hợp lệ luôn xuất được mà không phải xử lý thủ
  công hay gọi TTS lại; đóng app chờ worker kết thúc.

### Mốc 5 — Hồi quy, tài liệu, candidate Windows

- Chạy test mục 11; cập nhật context chuyên đề/hướng dẫn đúng hành vi đã làm.
- Nếu bước triển khai có yêu cầu build: build hai brand bằng builder hiện có,
  thêm SRT module trong verifier, kiểm source/PYZ/assets/worker/startup/ZIP.
- Giữ release cũ; build mới không đồng nghĩa provider live đã nghiệm thu.
- API/WordPress/Android không tự cập nhật theo desktop feature này.

## 11. Kiểm thử và tiêu chí nghiệm thu

Test mới dự kiến trong repo gốc: `tests/test_everai_srt_project.py`,
`tests/test_everai_srt_audio.py`, `tests/test_everai_srt_qt.py`.
Dùng tmp_path/grant test/renderer offline; không ghi ví production hoặc gọi
provider có phí. Test FFmpeg thực trên audio tổng hợp, không chỉ mock command.

Các nhóm bắt buộc:

1. Parser: BOM/CRLF/Unicode/multiline/markup, thiếu cue/end<=start, cue trùng STT,
   out-of-order, overlap lồng nhau và tiếp giáp chính xác; lỗi có cue/dòng.
2. Chunk: một cue >450/>350, nhiều cue nhỏ không ghép chung; kết quả worker
   hoàn thành đảo thứ tự vẫn ghép chunk/cue đúng; text có '-->' không bị parse.
3. Timeline: leading/inter-cue/trailing silence; cue ngắn/dài/sát ngưỡng/force;
   atempo thực tế lệch dự kiến; total_frame và cue frame count đúng tuyệt đối.
4. Alignment: dùng tone/impulse nguồn offline tại các cue để đo placement trong
   PCM, sai lệch tối đa 1 sample do harness; content không bị mất đầu/cuối.
   Với TTS thật chỉ kiểm buffer/timestamp, không hứa âm vị đầu đúng sample.
5. Identity: sửa timestamp không TTS; sửa text chỉ cue đó có generation mới;
   đổi giọng/speed invalidates đúng cue; cache hỏng bị loại; tạo lại chủ động
   có phí mới; không tái sử dụng free asset từ job khác bằng sửa fingerprint.
6. Recovery: crash sau paid trước manifest, sau child publish trước parent save,
   giữa fit/export; raw completed mất/hỏng; đường dẫn không hợp lệ; JSON corrupt;
   mất điện/thiếu đĩa không publish file “hoàn tất” giả.
7. Wallet: cùng job/chunk commit một lần, release held sau lỗi/hủy; tổng phí
   bằng chunk thực; hết hạn vẫn fit/export từ raw có sẵn mà không render mới;
   không yêu cầu active license trong nhánh hậu kỳ không phát sinh TTS.
8. Concurrency: tổng request workers không K²; không enqueue vô hạn; cancel
   ngừng việc mới, chờ child kết thúc; project lock giữa hai app; số dư đổi
   bởi process khác được báo rõ, giữ cue đã làm.
9. Quy mô: 10.000 cue ngắn, timeline nhiều giờ/gap lớn, RAM buffer hữu hạn và
   không tạo full-length stem theo cue; check disk/RIFF limits trước export.
10. Qt: restore/mode switch không mất draft, stats/chunk phí đúng, cue errors,
    dark/light/900×620 không overflow, worker callback đúng main thread;
    WAV/player, provider canonical IDs và hai brand.

Lệnh dự kiến từ repo gốc sau triển khai:

    python -m pytest tests/test_everai.py tests/test_everai_srt_project.py tests/test_everai_srt_audio.py tests/test_everai_srt_qt.py -q
    python -m pytest tests/test_everai_qt.py tests/test_everai_studio.py tests/test_everai_desktop_fixes.py tests/test_everai_txt_queue.py tests/test_everai_license.py tests/test_everai_capcut.py tests/test_everai_saydi.py -q

Nếu đổi helper player/audio/publish dùng chung, thêm tests/test_audio_player.py;
nếu đổi engine contracts chung thì thêm tests/test_api_server.py. Test mới chỉ
được ghi pass khi đã thực sự chạy. Lượt lập kế hoạch chỉ chạy bộ bắt buộc hiện có.

## 12. Quyết định cần giữ khi triển khai

- Bản đầu chặn overlap để bảo đảm giọng rõ và thuật toán ghép tuyến tính.
- Một giọng/project; không giả định provider có timestamp từng từ.
- Default auto 1.35x, force tối đa 4x; đều luôn xuất và không tự cắt lời. WAV
  là bản chuẩn sample timeline; có thể dài hơn mốc SRT cuối nếu tổng lời dư.
- Giữ audio raw sau export để chỉnh thời gian miễn gọi TTS; không hứa khôi
  phục miễn phí khi người dùng đã xóa raw và core đã dọn chunk.
- Job con kế thừa ledger/fingerprint, không thêm bypass/cross-job free cache.
- Đã hoàn thành các mốc 1–5, bao gồm hai candidate Windows đã qua verifier.
  Snapshot test/build hiện hành được ghi tại CURRENT_STATE.md. Các mô tả
  mốc/test trong tài liệu này là hợp đồng nghiệm thu cho lượt sửa tiếp theo.
