# EverAI TTS Studio

Tài liệu được tập trung tại [`.AGENTS/context project/`](.AGENTS/context%20project/README.md).

- [Hướng dẫn sử dụng](.AGENTS/context%20project/USER_GUIDE.md).
- [Trạng thái mới nhất](.AGENTS/context%20project/CURRENT_STATE.md).
- [Cấp key và phục hồi admin](.AGENTS/context%20project/ADMIN_KEY_GUIDE.md).
- [Hướng dẫn tích hợp WordPress & Slide Presentation](.AGENTS/context%20project/WORDPRESS_INTEGRATION_GUIDE.md).

Đường dẫn EXE/ZIP hiện hành, hash và bằng chứng kiểm tra được ghi tại
[CURRENT_STATE.md](.AGENTS/context%20project/CURRENT_STATE.md).

Phân hệ WordPress Provider mới:
- API Server: `api_server.py` (Zero-dependency `ThreadingHTTPServer` trên VPS cổng 8000).
- Plugin WordPress đóng gói sẵn: `dist/everai-tts-provider.zip`.
- Bản quyền bảo toàn: Giới hạn 2.000.000 ký tự (2 triệu ký tự), thời hạn 30 ngày qua Ed25519 offline wallet.

Bản Android độc lập đang nghiệm thu: [trạng thái, build và kiểm thử](.AGENTS/context%20project/ANDROID_BUILD_AND_TEST.md).
APK ứng viên đã ký nằm trong `dist/android-candidate/`; chưa xác nhận đủ điều kiện giao khách.
