# CodeGraph — workspace PC

CodeGraph hiện hành nằm tại `D:/EverAI_PC_Working/.codegraph` và index toàn bộ
source Python của workspace mới.

Trạng thái lần đồng bộ 27/10/2026:

- 50 file
- 1.560 node
- 5.270 edge
- SQLite database khoảng 6,66 MB

## Quy trình

Chạy từ `D:/EverAI_PC_Working`:

```powershell
codegraph status
codegraph explore "<symbol hoặc câu hỏi>"
codegraph sync
```

Dùng `explore` trước grep/đọc file khi cần hiểu symbol, caller/callee, execution
flow hoặc blast radius. Chỉ `sync` sau khi source code thay đổi; tài liệu
Markdown không tạo node nên việc di chuyển/cập nhật context có thể vẫn báo graph
đã up to date.

Graph này không chứa Android, API server, WordPress, admin key hoặc repository
cũ. Các số liệu CodeGraph cũ trong tài liệu lịch sử không còn áp dụng.

