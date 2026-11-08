# Bộ mẫu giọng offline

49 mẫu gốc tương ứng 49 ID trong voices.py, lấy từ nút nghe thử công khai trên https://www.everai.vn/text-to-speech ngày 18/10/2026.

Manifest ghi nguồn từng mẫu (không lưu query/chữ ký URL), SHA256 nguồn và SHA256 file cục bộ. Một số URL nguồn đuôi MP3 thực chất là WAV; helper chuyển sang MP3 để player native đọc đúng. Không dùng API tạo TTS để chuẩn bị bộ này.

Chạy `python prepare_voice_samples.py` để kiểm tra/hoàn thiện bộ mẫu. Builder tự kiểm tra đủ ID, byte/hash và nhúng đúng các MP3 được khai báo. Không đặt audio người dùng vào thư mục này.

Mẫu gốc không phụ thuộc model/tốc độ/cao độ trong form. Nút Nghe theo tùy chỉnh vẫn dùng luồng tạo/cache riêng. Cần xác minh quyền tái phân phối demo trước phát hành thương mại; hồ sơ nguồn không thay giấy phép của bên sở hữu.
