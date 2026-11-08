# Voice Pro PC — điểm vào module

Context chính nằm ở root workspace:
[`../../.AGENTS/context project/`](../../.AGENTS/context%20project/README.md).

Đọc `CURRENT_STATE.md` trước, sau đó `AGENT_ONBOARDING.md`. Dùng CodeGraph ở
root workspace trước khi tra cứu code. Giữ module độc lập và không khôi phục
Android, API server, WordPress, ElevenLabs, Edge hoặc Saydi nếu chưa có yêu cầu
mới rõ ràng từ người dùng.

Kiểm tra bắt buộc từ `D:/EverAI_PC_Working`:

```powershell
pytest tests/test_everai.py
```

