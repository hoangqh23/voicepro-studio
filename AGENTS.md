# Voice Pro PC — hướng dẫn agent

Workspace hiện hành: `D:/EverAI_PC_Working`.

Đây là workspace độc lập chỉ dành cho tool Windows Voice Pro/EverAI. Android,
API server, WordPress, ElevenLabs, Edge và Saydi không thuộc source hiện hành.

## Điểm vào

- Chạy từ root: `python run_everai_gui.py`
- Chạy module: `cd src/everai; python run.py`
- Test bắt buộc từ root: `pytest tests/test_everai.py`
- Build Windows: `python src/everai/build_voicepro.py --dist <thu-muc-dich>`

## Context

Đọc theo thứ tự:

1. [`.AGENTS/context project/CURRENT_STATE.md`](.AGENTS/context%20project/CURRENT_STATE.md)
2. [`.AGENTS/context project/AGENT_ONBOARDING.md`](.AGENTS/context%20project/AGENT_ONBOARDING.md)
3. [`.AGENTS/context project/README.md`](.AGENTS/context%20project/README.md)

## Quy tắc

- Dùng CodeGraph tại `.codegraph/` trước khi dò hoặc đọc code.
- Giữ `src/everai` độc lập; không thêm phụ thuộc vào repository cũ.
- Không đưa private key, mật khẩu, credential, ví hoặc dữ liệu khách vào
  source, log, chat hay bản phát hành.
- `src/everai/admin_key` chứa bộ admin gốc đã phục hồi; không tự tạo, thay,
  xoay khóa hoặc đưa folder này vào bản phát hành khách. Backup phục hồi độc
  lập vẫn được giữ tại `D:/EverAI_Core_Final_20261027`.
- Sau thay đổi code luôn chạy ít nhất `pytest tests/test_everai.py` từ root.

