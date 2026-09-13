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

## Engine xử lý & Chiến lược Auto Smart

V2 không còn dùng môn học để chọn watermark engine. Router dựa trên cấu trúc thật của PDF và độ tin cậy:

- **Auto Smart (Mặc định khuyến nghị):** Phân tích PDF tự động một lần và chọn cách xử lý nhanh, an toàn nhất cho từng tài liệu. Đây là lựa chọn tốt nhất cho hầu hết mọi trường hợp.
- **Stream Clean:** Chuyên dụng cho PDF gốc dạng text/vector; loại bỏ watermark lặp lại trong content stream/XObject mà không rasterize toàn trang, giữ nguyên 100% độ sắc nét vector và chữ.
- **Raster Clean:** Dành cho PDF scan/ảnh toàn trang; ưu tiên trích xuất trực tiếp native image XObject ở độ phân giải gốc, học mẫu watermark mức tài liệu và tái tạo nền tối thiểu.
- **Compatibility Clean:** Dành cho PDF phức tạp hoặc khi các engine nhanh chưa đủ độ tin cậy cao, đảm bảo đường xử lý an toàn nhất.

### Compatibility Gates & Fallback

Khi người dùng chọn engine thủ công (ví dụ: ép `stream_clean` cho PDF thuần scan ảnh, hoặc `raster_clean` cho PDF thuần vector), hệ thống kiểm tra qua **Compatibility Gates**. Nếu tài liệu không đáp ứng điều kiện tiên quyết của engine đã chọn hoặc vi phạm an toàn, router tự động thông báo và chuyển hướng sang chiến lược tương thích an toàn (hoặc fallback legacy) thay vì làm hỏng tài liệu.

OCR **không phải yêu cầu bắt buộc theo từng trang**. Thiết kế V2 chỉ cho phép OCR dạng lazy/optional ở ROI khi thật sự cần; các đường native/template hiện tại không cần full-document OCR.

## Phân biệt: Engine preference vs Content protection profile

Hai cài đặt này phục vụ hai mục đích hoàn toàn khác nhau trong kiến trúc V2:

1. **Engine preference (Bước 3 trên giao diện chính):** Quyết định **loại engine xử lý** cấp tài liệu (`auto_smart`, `stream_clean`, `raster_clean`, `compatibility_clean`).
2. **Content protection profile (Cài đặt nâng cao → Bảo vệ nội dung — Auto V2):** Cung cấp **gợi ý bảo vệ nội dung cho router**, giúp router tinh chỉnh các bộ lọc bảo vệ pixel/vector theo đặc thù tài liệu:
   - **Auto:** Cân bằng tự động giữa xóa watermark và bảo toàn nội dung.
   - **Formula & Diagram Safe (Toán / biểu đồ):** Ưu tiên giữ công thức, bảng biểu, đồ thị và đường kẻ mảnh.
   - **Diagram & Line Safe (Vật lý):** Ưu tiên sơ đồ mạch, hình minh họa thí nghiệm và các nét vẽ.
   - **Symbol & Structure Safe (Hóa học):** Bảo vệ ký hiệu nguyên tố, chỉ số nhỏ, liên kết và cấu trúc phân tử.
   - **Text & Image Safe (Ebook):** Giữ khối chữ dài, ảnh chụp, bìa màu và bố cục sách.

*Lưu ý:* Profile bảo vệ nội dung **không** chọn watermark engine trực tiếp; nó chỉ cung cấp trọng số bảo vệ an toàn cho router.

## Làm sạch chân trang (Footer Cleanup)

Với các tài liệu có watermark URL chân trang (như TaiLieuOnThi), hệ thống trang bị công nghệ **Native Footer Polish** hoạt động trực tiếp ở độ phân giải ảnh gốc, loại bỏ triệt để vết mờ URL mà không tạo vệt trắng hình chữ nhật (white patch), không làm lộ tone seam hay quầng sáng viền:

- **Auto (Mặc định):** Tự động đo điểm tương phản còn sót (residual score) ở chân trang. Bắt đầu bằng mức *Standard*; nếu vệt mờ vẫn vượt ngưỡng, app tự động leo thang lên *Deep*.
- **Standard:** Mức làm sạch tiêu chuẩn cho vùng URL chân trang, giữ nguyên cấu trúc nền cục bộ.
- **Deep:** Làm sạch chuyên sâu với ngưỡng tương phản nhạy hơn và vùng phủ rộng hơn cho các vệt mờ cứng đầu.

**Vùng bảo vệ bất khả xâm phạm (Guards):**
Cả hai mức *Standard* và *Deep* đều tuân thủ nghiêm ngặt các guard:
- **Số trang (Page Number Guard):** Vùng số trang bên phải được bảo vệ tuyệt đối, không bao giờ bị lem hay mờ.
- **Dòng thông tin giáo viên / Slogan (Color Guard & Rule Guard):** Các dòng chữ có màu (đỏ, xanh), slogan và đường phân cách ngang hợp lệ phía trên chân trang được giữ nguyên vẹn 100%.

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

## Preset & Hiệu năng an toàn (Safe Performance)

Hệ thống cung cấp 4 preset chuẩn hóa, đảm bảo tính bất biến (invariants) về chất lượng và độ an toàn:

- **Fast:** Dành cho công việc cần ưu tiên tốc độ (speed-sensitive work) với các giá trị DPI và chất lượng thấp hơn hiện hành (`dpi: 200`, `output_dpi: 200`, `quality: 88`, auto workers).
- **Balanced:** Mặc định khuyến nghị (recommended default), cân bằng tối ưu giữa tốc độ xử lý và độ sắc nét tài liệu (`dpi: 240`, `output_dpi: 240`, `quality: 92`, auto workers).
- **High Quality:** Ưu tiên độ trung thực cao hơn (higher fidelity), thời gian xử lý chậm hơn (slower) (`dpi: 320`, `output_dpi: 320`, `quality: 95`, auto workers).
- **Safe:** Chế độ bảo thủ chạy một worker (conservative single-worker mode), giới hạn tài nguyên để tránh nghẽn RAM/CPU trên máy yếu hoặc file phức tạp (`dpi: 240`, `output_dpi: 240`, `quality: 95`, worker request = 1).

### Nguyên tắc chất lượng & hiệu năng bất biến

- **Footer visual QC remains active for every preset:** Cơ chế kiểm tra và làm sạch chân trang (Footer visual QC & Polish) mặc định là `auto` trên mọi preset. Preset Fast tuyệt đối không tắt hay bỏ qua bước làm sạch chân trang.
- **Analysis cache applies only to unchanged files within the current app process:** Bộ nhớ đệm phân tích cấu trúc tài liệu V2 chỉ áp dụng cho các file hoàn toàn không thay đổi (xác thực qua kích thước file, mtime và mẫu băm SHA-256). Cache này chỉ tồn tại trong bộ nhớ của tiến trình ứng dụng hiện tại (in-process), không lưu ra đĩa và tự động bị hủy/làm mới khi file có thay đổi.
- **Auto Smart still owns safe strategy selection unless the user requests a compatibility-gated engine preference:** Engine `Auto Smart` luôn toàn quyền sở hữu việc lựa chọn chiến lược an toàn nhất cho từng tài liệu dựa trên cấu trúc thực tế, trừ khi người dùng chủ động yêu cầu một engine preference cụ thể đã qua cổng kiểm tra tương thích (compatibility gates).

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
