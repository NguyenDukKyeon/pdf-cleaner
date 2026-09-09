# PDF_Cleaner — Desktop Native

Ứng dụng cá nhân để làm sạch watermark PDF trên Windows.

## Cách mở

### Lần đầu

Double-click `PDF_Cleaner_App.vbs`.

Nếu chưa có `.venv`, launcher sẽ tự mở `run_windows.bat` để cài thư viện một lần rồi mở app.

### Những lần sau

Chỉ cần double-click:

```text
PDF_Cleaner_App.vbs
```

App mở bằng **pywebview + Edge WebView2**, không chạy FastAPI, không mở localhost và không mở trình duyệt. Python chạy bằng `pythonw.exe` nên không có cửa sổ console đi kèm.

Bản này cố định xử lý **1 Python process** để không sinh thêm worker Python trong lúc xử lý.

## Luồng xử lý

1. Chọn một hoặc nhiều PDF trực tiếp trên máy.
2. Chọn nơi lưu; mặc định tạo `<tên>_clean.pdf`.
3. Chọn Toán / Lý / Hóa / Ebook và preset.
4. App xử lý vào file staging, chạy QC rồi mới đưa ra file kết quả.
5. Nếu QC lỗi thật, file staging bị xóa; app không tạo thư mục `_failed_*` cạnh tài liệu.
6. Nếu bật ghi đè, file gốc được backup vào `_backup` trước khi thay thế.

Nếu `<tên>_clean.pdf` đã tồn tại và **không** bật ghi đè, app tự tạo `<tên>_clean_2.pdf`, `_3.pdf`... thay vì ghi đè kết quả cũ.

## Preset

- **Fast:** 200 DPI, JPEG 88.
- **Balanced:** 240 DPI, JPEG 92.
- **High Quality:** 320 DPI, JPEG 95.
- **Safe Mode:** 240 DPI, JPEG 95.

Tất cả preset trong bản desktop này đều dùng một process.

## File chính

```text
PDF_Cleaner_App.vbs   launcher hằng ngày
run_windows.bat       cài/cập nhật dependency khi cần
desktop_app.py        cửa sổ pywebview
native_api.py         cầu nối JavaScript ↔ Python
backend/service.py    job/QC/output logic
backend/engine/       engine xử lý PDF
frontend/             giao diện desktop
```
