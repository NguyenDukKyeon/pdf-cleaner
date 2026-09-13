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
- **Stream Clean:** Chuyên dụng cho PDF gốc dạng text/vector; loại bỏ watermark lặp lại trong content stream/XObject mà không rasterize toàn trang, nhờ đó giữ text/vector ở dạng cấu trúc khi đường này áp dụng an toàn.
- **Raster Clean:** Dành cho PDF scan/ảnh toàn trang; ưu tiên trích xuất trực tiếp native image XObject ở độ phân giải gốc, học mẫu watermark mức tài liệu và tái tạo nền tối thiểu.
- **Compatibility Clean:** Dùng pipeline legacy tương thích cho PDF phức tạp hoặc trường hợp người dùng chủ động yêu cầu đường xử lý bảo thủ hơn.

### Compatibility Gates & Fallback

Khi người dùng chọn engine thủ công (ví dụ: ép `stream_clean` cho PDF thuần scan ảnh, hoặc `raster_clean` cho PDF thuần vector), hệ thống vẫn chạy analyzer và kiểm tra **Compatibility Gates**. Nếu tài liệu không tương thích, job bị chặn **trước khi xử lý phá hủy** và báo lý do rõ ràng; người dùng có thể chọn **Auto Smart** hoặc **Compatibility Clean**. Hệ thống không âm thầm đổi manual preference sang một engine khác.

Riêng với **Auto Smart**, nếu chiến lược V2 đang chạy gặp lỗi an toàn, executor vẫn có thể dùng legacy fallback theo cấu hình hiện hành.

OCR **không phải yêu cầu bắt buộc theo từng trang**. Thiết kế V2 chỉ cho phép OCR dạng lazy/optional ở ROI khi thật sự cần; các đường native/template hiện tại không cần full-document OCR.

## Phân biệt: Engine preference vs Content protection profile

Hai cài đặt này phục vụ hai mục đích hoàn toàn khác nhau trong kiến trúc V2:

1. **Engine preference (Bước 3 trên giao diện chính):** Chọn preference cấp tài liệu (`auto_smart`, `stream_clean`, `raster_clean`, `compatibility_clean`). Các preference thủ công vẫn phải qua compatibility gate.
2. **Content protection profile (Cài đặt nâng cao → Bảo vệ nội dung — Auto V2):** Cung cấp **gợi ý bảo vệ nội dung cho router**, giúp router tinh chỉnh mức bảo thủ theo đặc thù tài liệu:
   - **Auto:** Cân bằng tự động giữa xóa watermark và bảo toàn nội dung.
   - **Formula & Diagram Safe (Toán / biểu đồ):** Ưu tiên giữ công thức, bảng biểu, đồ thị và đường kẻ mảnh.
   - **Diagram & Line Safe (Vật lý):** Ưu tiên sơ đồ mạch, hình minh họa thí nghiệm và các nét vẽ.
   - **Symbol & Structure Safe (Hóa học):** Bảo vệ ký hiệu nguyên tố, chỉ số nhỏ, liên kết và cấu trúc phân tử.
   - **Text & Image Safe (Ebook):** Giữ khối chữ dài, ảnh chụp, bìa màu và bố cục sách.

*Lưu ý:* Profile bảo vệ nội dung **không** chọn watermark engine trực tiếp.

## Làm sạch chân trang (Footer Cleanup)

Với các tài liệu có watermark URL chân trang như TaiLieuOnThi, **Native Footer Polish** xử lý cục bộ ở độ phân giải ảnh gốc để giảm vệt mờ URL còn sót mà không rasterize lại toàn bộ trang:

- **Auto (Mặc định):** Đo residual score ở chân trang. Nếu vùng đã đạt ngưỡng sạch thì không sửa thêm; nếu chưa đạt, chạy *Standard* rồi tự nâng lên *Deep* khi cần.
- **Standard:** Mức làm sạch tiêu chuẩn cho vùng URL chân trang, giữ nguyên cấu trúc nền cục bộ.
- **Deep:** Làm sạch mạnh hơn trong cùng vùng tin cậy cho các vệt mờ cứng đầu.

**Các vùng bảo vệ:**
- **Page Number Guard:** Loại vùng số trang bên phải khỏi mask sửa footer.
- **Color/Rule Guard:** Loại chữ màu và đường phân cách ngang hợp lệ khỏi mask sửa.
- QC đo thêm `footer_residual_score` và `footer_protected_change_ratio`; nếu vượt ngưỡng, staging không được promote thành output cuối.

## Tiến trình và báo cáo

Job đi qua các stage:

```text
QUEUED → ANALYZING → PLANNING → PROCESSING → VERIFYING → DONE
```

Khi có lỗi hoặc người dùng hủy, trạng thái kết thúc là `FAILED` hoặc `CANCELLED`.

UI hiển thị các diagnostics khi có dữ liệu: loại PDF, watermark, chiến lược, confidence, worker count, số trang dùng native image, số lần OCR, mức Footer cleanup thực tế và footer residual score. Processing report còn lưu changed pages/items, fallback reason và thời gian analysis/processing/QC.

Worker count không còn bị cố định ở 1. Chế độ auto có thể chọn nhiều worker khi CPU/RAM/page count cho phép; **Safe** hoặc điều kiện tài nguyên hạn chế vẫn có thể chạy 1 worker.

## Quality Control (QC)

V2 kiểm tra nhiều lớp trước khi chấp nhận output:

- PDF output mở được, số trang và geometry hợp lệ.
- Bảo toàn text/vector khi input có text layer có ý nghĩa.
- Với raster, hạn chế thay đổi pixel ngoài vùng watermark dự đoán.
- Kiểm tra watermark residual sau xử lý.
- Với TaiLieuOnThi-guided, kiểm tra thêm footer residual/protected-change cùng các guard native ngoài vùng tin cậy.
- Chỉ promote staging khi QC đạt; overwrite vẫn giữ backup.

## Preset & Hiệu năng an toàn (Safe Performance)

Hệ thống cung cấp 4 preset chuẩn hóa:

- **Fast:** ưu tiên tốc độ (`dpi: 200`, `output_dpi: 200`, `quality: 88`, auto workers).
- **Balanced:** mặc định khuyến nghị (`dpi: 240`, `output_dpi: 240`, `quality: 92`, auto workers).
- **High Quality:** ưu tiên fidelity cao hơn (`dpi: 320`, `output_dpi: 320`, `quality: 95`, auto workers).
- **Safe:** bảo thủ và yêu cầu một worker (`dpi: 240`, `output_dpi: 240`, `quality: 95`, worker request = 1).

### Nguyên tắc chất lượng & hiệu năng bất biến

- **Footer visual QC vẫn hoạt động ở mọi preset:** Fast không được tắt footer residual QC.
- **Analysis cache chỉ áp dụng cho file không đổi trong tiến trình app hiện tại:** cache key gồm fingerprint file và các tùy chọn phân tích; cache không lưu ra đĩa.
- **Staged analysis:** analyzer có thể dừng ở 3 hoặc 5 trang khi bằng chứng đã đủ mạnh; trường hợp mơ hồ mở rộng tới 8 trang. Mỗi stage raster dùng đúng tập trang của stage đó khi probe watermark.
- **Auto Smart vẫn sở hữu strategy selection mặc định:** manual preference chỉ được dùng khi compatibility gate cho phép.

Các giá trị DPI/JPEG chi tiết có thể xem/chỉnh ở tab **Cài đặt nâng cao**.

## Troubleshooting

### App không mở

Chạy `run_windows.bat` một lần để cài/cập nhật dependency, sau đó mở lại `PDF_Cleaner_App.vbs`.

### Auto dùng legacy fallback

Đây không mặc định là lỗi. Router/executor có thể fallback khi confidence chưa đủ hoặc chiến lược V2 không đạt điều kiện an toàn. Xem phần **Log kết quả** và diagnostics để biết strategy/fallback reason.

### Manual engine bị từ chối

Nếu `Stream Clean` hoặc `Raster Clean` không tương thích với cấu trúc PDF, app chặn trước khi xử lý. Chuyển sang **Auto Smart** để router tự chọn hoặc **Compatibility Clean** nếu muốn dùng pipeline tương thích.

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
