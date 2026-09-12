# PDF_Cleaner — Desktop Native

Ứng dụng desktop Windows để tự động phân tích và làm sạch watermark PDF, ưu tiên giữ nguyên cấu trúc/vector và chỉ dùng đường raster khi tài liệu thực sự là ảnh.

## Cách mở

### Lần đầu

Double-click `PDF_Cleaner_App.vbs`.

Nếu chưa có `.venv`, launcher sẽ tự mở `run_windows.bat` để cài dependency một lần rồi mở app.

### Những lần sau

Chỉ cần double-click:

```text
PDF_Cleaner_App.vbs
```

App chạy bằng **pywebview + Edge WebView2**, không dùng FastAPI, không mở localhost và không mở trình duyệt. Python chạy bằng `pythonw.exe` nên không có cửa sổ console đi kèm.

## Luồng Auto V2

1. Chọn một hoặc nhiều PDF trực tiếp trên máy.
2. Chọn nơi lưu; mặc định tạo `<tên>_clean.pdf`.
3. Chọn preset chất lượng: **Fast / Balanced / High Quality / Safe**.
4. Bấm **Xử lý PDF**. Auto V2 phân tích tài liệu một lần rồi tự chọn chiến lược phù hợp.
5. App xử lý vào file staging, chạy QC rồi mới promote sang file kết quả.
6. Nếu QC thất bại, staging bị loại bỏ và file gốc/final không bị thay thế.
7. Nếu bật ghi đè, file gốc được backup vào `_backup` trước khi thay thế.

Nếu `<tên>_clean.pdf` đã tồn tại và **không** bật ghi đè, app tự tạo `<tên>_clean_2.pdf`, `_3.pdf`... thay vì ghi đè kết quả cũ.

## Auto chọn chiến lược như thế nào?

V2 không còn dùng môn học để chọn watermark engine. Router dựa trên cấu trúc thật của PDF và độ tin cậy:

- **Structural / stream path:** loại watermark lặp lại ở content stream/object khi có thể cô lập an toàn, không rasterize toàn trang.
- **Raster template path:** với PDF là ảnh toàn trang, ưu tiên lấy trực tiếp native image XObject, học watermark ở mức tài liệu rồi sửa tối thiểu các vùng tin cậy.
- **TaiLieuOnThi-guided path:** watermark TaiLieuOnThi có bằng chứng đủ mạnh có thể dùng cleanup chuyên biệt trên ảnh native, sau đó QC kiểm tra residual và thay đổi ngoài vùng watermark.
- **Legacy fallback:** nếu confidence thấp hoặc V2 gặp lỗi an toàn, app giữ các engine TDM/IPCLASS/TYHH/Ebook làm fallback tương thích.

OCR **không phải yêu cầu bắt buộc theo từng trang**. Thiết kế V2 chỉ cho phép OCR dạng lazy/optional ở ROI khi thật sự cần; các đường native/template hiện tại không cần full-document OCR.

## Advanced: Content protection profile

Trong **Cài đặt nâng cao → Bảo vệ nội dung — Auto V2**, có các profile:

- Auto
- Toán / biểu đồ
- Vật lý
- Hóa học
- Ebook

Đây chỉ là **gợi ý bảo vệ nội dung cho router**, không trực tiếp chọn watermark engine.

## Tiến trình và báo cáo

Job đi qua các stage:

```text
QUEUED → ANALYZING → PLANNING → PROCESSING → VERIFYING → DONE
```

Khi có lỗi hoặc người dùng hủy, trạng thái kết thúc là `FAILED` hoặc `CANCELLED`.

UI hiển thị các diagnostics khi có dữ liệu: loại PDF, watermark, chiến lược, confidence, worker count, số trang dùng native image và số lần OCR. Processing report còn lưu changed pages/items, fallback reason và thời gian analysis/processing/QC.

Worker count không còn bị cố định ở 1. Chế độ auto có thể chọn nhiều worker khi CPU/RAM/page count cho phép; **Safe** hoặc điều kiện tài nguyên hạn chế vẫn có thể chạy 1 worker.

## Quality Control (QC)

V2 kiểm tra nhiều lớp trước khi chấp nhận output:

- PDF output mở được, số trang và geometry hợp lệ.
- Bảo toàn text/vector khi input có text layer có ý nghĩa.
- Với raster, hạn chế thay đổi pixel ngoài vùng watermark dự đoán.
- Kiểm tra watermark residual sau xử lý.
- Với đường TaiLieuOnThi-guided, có thêm guard về thay đổi ngoài vùng tin cậy và kích thước output.
- Chỉ promote staging khi QC đạt; overwrite vẫn giữ backup.

## Preset

- **Fast:** ưu tiên tốc độ.
- **Balanced:** mặc định, cân bằng tốc độ/chất lượng.
- **High Quality:** ưu tiên chất lượng.
- **Safe:** bảo thủ hơn và giới hạn worker.

Các giá trị DPI/JPEG chi tiết có thể xem/chỉnh ở tab **Cài đặt nâng cao**.

## Troubleshooting

### App không mở

Chạy `run_windows.bat` một lần để cài/cập nhật dependency, sau đó mở lại `PDF_Cleaner_App.vbs`.

### Auto dùng legacy fallback

Đây không mặc định là lỗi. Router có thể fallback khi confidence chưa đủ hoặc chiến lược V2 không đạt điều kiện an toàn/QC. Xem phần **Log kết quả** và diagnostics để biết strategy/fallback reason.

### File không được thay thế sau khi xử lý

Kiểm tra log QC. Nếu QC thất bại, app cố ý không promote staging để tránh làm hỏng file gốc hoặc output trước đó.

### PDF ảnh xử lý lâu

Raster cleanup vẫn nặng hơn structural removal. Auto ưu tiên native image extraction và document-level analysis để tránh render/OCR dư thừa; worker count còn phụ thuộc RAM, CPU và số trang.

## File chính

```text
PDF_Cleaner_App.vbs            launcher hằng ngày
run_windows.bat                cài/cập nhật dependency khi cần
desktop_app.py                 cửa sổ pywebview
native_api.py                  cầu nối JavaScript ↔ Python
backend/service.py             compatibility façade/job API
backend/app/                   processing/QC/output services
backend/engine/analyzer/       phân tích cấu trúc tài liệu
backend/engine/router/         chọn ProcessingPlan
backend/engine/strategies/     structural/raster/legacy strategies
backend/engine/pipeline_v2/    analyze → plan → execute/report
backend/engine/qc_v2/          QC V2
frontend/                      giao diện desktop Auto-first
tests/                         unit/integration/frontend regression tests
```

## Test

```bash
python -m pytest -q
```

CI còn chạy syntax gates cho Python production modules và `frontend/static/app.js` trước full test suite.
