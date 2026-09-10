from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Native bridge: preserve the public methods while guaranteeing the new Auto V2 defaults.
replace_once(
    "native_api.py",
    '''    def start_process(self, payload: dict[str, Any]) -> dict[str, Any]:\n        return self._safe(lambda: self._service().start_process_local(payload))\n''',
    '''    def start_process(self, payload: dict[str, Any]) -> dict[str, Any]:\n        normalized = dict(payload or {})\n        normalized.setdefault("mode", "auto")\n        normalized.setdefault("content_profile", "auto")\n        return self._safe(lambda: self._service().start_process_local(normalized))\n''',
)

# Frontend: Auto is the primary workflow; subject profiles become Advanced safety hints.
replace_once(
    "frontend/index.html",
    '<p>Làm sạch watermark PDF scan</p>',
    '<p>Tự động phân tích và làm sạch watermark PDF</p>',
)
replace_once(
    "frontend/index.html",
    '''                <h2>Chế độ xử lý</h2>\n                <p>Mặc định dùng Lý - IPCLASS từ engine mới; có thể đổi mode nếu cần.</p>''',
    '''                <h2>Chế độ xử lý tự động</h2>\n                <p>Auto V2 phân tích cấu trúc PDF rồi chọn chiến lược phù hợp; bạn chỉ cần chọn mức chất lượng.</p>''',
)
replace_once(
    "frontend/index.html",
    '''            <div id="modePicker" class="picker-grid mode-picker" role="radiogroup" aria-label="Loại tài liệu">\n              <button class="pick-card" data-mode="toan"><span>∑</span><strong>Toán - TDM</strong></button>\n              <button class="pick-card selected" data-mode="ly"><span>⚛</span><strong>Lý - IPCLASS</strong></button>\n              <button class="pick-card" data-mode="hoa"><span>⚗</span><strong>Hóa - TYHH</strong></button>\n              <button class="pick-card" data-mode="ebook"><span>📖</span><strong>Ebook</strong></button>\n            </div>''',
    '''            <div class="auto-mode-card" aria-label="Auto V2">\n              <span class="auto-mode-badge">AUTO V2</span>\n              <div>\n                <strong>Tự động nhận dạng loại PDF và watermark</strong>\n                <small>Ưu tiên giữ cấu trúc PDF; chỉ rasterize khi thực sự cần.</small>\n              </div>\n            </div>''',
)
replace_once(
    "frontend/index.html",
    '<button class="preset-card" data-preset="safe_mode"><b>Safe Mode</b><small>An toàn, 1 CPU</small></button>',
    '<button class="preset-card" data-preset="safe_mode"><b>Safe</b><small>Bảo thủ, chạy 1 worker</small></button>',
)
replace_once(
    "frontend/index.html",
    '''            <div id="statusText" class="status-text">Sẵn sàng • Hãy chọn file PDF để bắt đầu.</div>\n          </article>''',
    '''            <div id="statusText" class="status-text">Sẵn sàng • Hãy chọn file PDF để bắt đầu.</div>\n\n            <ol id="stageList" class="stage-list" aria-label="Các giai đoạn xử lý V2">\n              <li data-stage="ANALYZING"><span class="stage-dot"></span><span>Phân tích PDF</span></li>\n              <li data-stage="PLANNING"><span class="stage-dot"></span><span>Chọn chiến lược</span></li>\n              <li data-stage="PROCESSING"><span class="stage-dot"></span><span>Xử lý watermark</span></li>\n              <li data-stage="VERIFYING"><span class="stage-dot"></span><span>Kiểm tra chất lượng</span></li>\n            </ol>\n\n            <div class="diagnostics-grid" aria-label="Tóm tắt phân tích tự động">\n              <div class="diagnostic"><span>Loại PDF</span><strong id="analysisRepresentation">—</strong></div>\n              <div class="diagnostic"><span>Watermark</span><strong id="analysisWatermark">—</strong></div>\n              <div class="diagnostic"><span>Chiến lược</span><strong id="analysisStrategy">—</strong></div>\n              <div class="diagnostic"><span>Độ tin cậy</span><strong id="analysisConfidence">—</strong></div>\n              <div class="diagnostic"><span>Workers</span><strong id="analysisWorkers">—</strong></div>\n              <div class="diagnostic"><span>Ảnh native</span><strong id="analysisNativeImage">—</strong></div>\n              <div class="diagnostic"><span>OCR</span><strong id="analysisOcrCalls">—</strong></div>\n            </div>\n          </article>''',
)
replace_once(
    "frontend/index.html",
    '''      <article class="card settings-card">\n        <h2>Tối ưu tốc độ an toàn</h2>''',
    '''      <article class="card settings-card">\n        <h2>Bảo vệ nội dung — Auto V2</h2>\n        <p>Đây chỉ là gợi ý an toàn cho router, không chọn watermark engine trực tiếp.</p>\n        <label class="form-field">\n          <span>Content protection profile</span>\n          <select id="contentProfile">\n            <option value="auto" selected>Auto</option>\n            <option value="math">Toán / biểu đồ</option>\n            <option value="physics">Vật lý</option>\n            <option value="chemistry">Hóa học</option>\n            <option value="ebook">Ebook</option>\n          </select>\n        </label>\n      </article>\n\n      <article class="card settings-card">\n        <h2>Tối ưu tốc độ an toàn</h2>''',
)
replace_once(
    "frontend/index.html",
    '''        <h2>Cài đặt theo loại tài liệu</h2>\n        <p>Phần này dành cho người đã hiểu config. Người dùng phổ thông chỉ cần tab Xử lý.</p>''',
    '''        <h2>Tương thích engine cũ</h2>\n        <p>Chỉ dùng để tinh chỉnh fallback legacy. Luồng Auto V2 không chọn engine theo môn học.</p>''',
)
replace_once(
    "frontend/index.html",
    '<label class="form-field"><span>Mode</span><select id="settingsMode">',
    '<label class="form-field"><span>Legacy engine config</span><select id="settingsMode">',
)
replace_once(
    "frontend/index.html",
    '<li>Chọn loại tài liệu Toán/Lý/Hóa/Ebook và preset tốc độ/chất lượng.</li>',
    '<li>Giữ Auto V2 làm mặc định và chọn preset tốc độ/chất lượng; profile nội dung nằm trong Cài đặt nâng cao.</li>',
)
replace_once(
    "frontend/index.html",
    '<span>Desktop-only • 1 Python process</span>',
    '<span>Desktop-only • Auto workers có giới hạn an toàn</span>',
)

# Frontend state and payloads.
replace_once(
    "frontend/static/app.js",
    "let selectedMode = 'ly';\nlet selectedPreset = 'balanced';",
    "let selectedMode = 'auto';\nlet selectedContentProfile = 'auto';\nlet selectedPreset = 'balanced';",
)
replace_once(
    "frontend/static/app.js",
    '''let presetValues = {\n  fast: { hint: 'Tốc độ cao, chất lượng vừa.', dpi: 200, output_dpi: 200, quality: 88, cpu: 1 },\n  balanced: { hint: 'Cân bằng giữa tốc độ và chất lượng.', dpi: 240, output_dpi: 240, quality: 92, cpu: 1 },\n  high_quality: { hint: 'Chất lượng cao nhất, chậm hơn.', dpi: 320, output_dpi: 320, quality: 95, cpu: 1 },\n  safe_mode: { hint: 'Thiết lập bảo thủ, chạy một tiến trình.', dpi: 240, output_dpi: 240, quality: 95, cpu: 1 },\n};''',
    '''let presetValues = {\n  fast: { hint: 'Tốc độ cao, chất lượng vừa.', dpi: 200, output_dpi: 200, quality: 88, cpu: 0 },\n  balanced: { hint: 'Cân bằng giữa tốc độ và chất lượng.', dpi: 240, output_dpi: 240, quality: 92, cpu: 0 },\n  high_quality: { hint: 'Chất lượng cao nhất, chậm hơn.', dpi: 320, output_dpi: 320, quality: 95, cpu: 0 },\n  safe_mode: { hint: 'Thiết lập bảo thủ, chạy một worker.', dpi: 240, output_dpi: 240, quality: 95, cpu: 1 },\n};''',
)
replace_once(
    "frontend/static/app.js",
    '''function selectMode(mode) {\n  selectedMode = mode;\n  $$('#modePicker .pick-card').forEach((card) => card.classList.toggle('selected', card.dataset.mode === mode));\n}\n''',
    '''const CONTENT_PROFILES = new Set(['auto', 'math', 'physics', 'chemistry', 'ebook']);\n\nfunction selectContentProfile(profile) {\n  selectedContentProfile = CONTENT_PROFILES.has(profile) ? profile : 'auto';\n  const select = $('#contentProfile');\n  if (select) select.value = selectedContentProfile;\n}\n''',
)

stage_helpers = r'''
const STAGE_SEQUENCE = ['ANALYZING', 'PLANNING', 'PROCESSING', 'VERIFYING'];
const STAGE_LABELS = {
  QUEUED: 'Đang chờ',
  ANALYZING: 'Đang phân tích PDF...',
  PLANNING: 'Đang chọn chiến lược...',
  PROCESSING: 'Đang xử lý watermark...',
  VERIFYING: 'Đang kiểm tra chất lượng...',
  DONE: 'Hoàn tất',
  FAILED: 'Xử lý thất bại',
  CANCELLED: 'Đã hủy',
};

function updateStageUi(stage) {
  const current = String(stage || 'QUEUED').toUpperCase();
  const currentIndex = STAGE_SEQUENCE.indexOf(current);
  $$('#stageList [data-stage]').forEach((item, index) => {
    item.classList.remove('active', 'done');
    if (current === 'DONE' || (currentIndex >= 0 && index < currentIndex)) item.classList.add('done');
    if (currentIndex === index) item.classList.add('active');
  });
  if (STAGE_LABELS[current]) $('#statusText').textContent = STAGE_LABELS[current];
}

function setDiagnostic(id, value) {
  const element = $(id);
  if (element) element.textContent = value == null || value === '' ? '—' : String(value);
}

function formatConfidence(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return `${Math.round(Math.max(0, Math.min(1, numeric)) * 100)}%`;
}

function renderDiagnostics(payload = {}) {
  const report = payload.report || {};
  const metadata = report.metadata || {};
  const kind = payload.document_kind || metadata.document_kind;
  const kindLabels = { vector: 'Vector', hybrid: 'Hybrid', raster: 'Raster', unknown: 'Chưa rõ' };
  const strategy = payload.strategy || report.strategy;
  const strategyLabels = {
    stream_remove: 'Stream remove',
    vector_remove: 'Vector remove',
    raster_template: 'Raster template',
    legacy: 'Legacy fallback',
  };
  const confidence = payload.strategy_confidence ?? payload.confidence ?? report.confidence;
  const watermark = payload.watermark_family || payload.marker || metadata.watermark_family;

  if (kind) setDiagnostic('#analysisRepresentation', kindLabels[String(kind).toLowerCase()] || kind);
  if (watermark) setDiagnostic('#analysisWatermark', watermark);
  if (strategy) setDiagnostic('#analysisStrategy', strategyLabels[strategy] || strategy);
  if (confidence != null) setDiagnostic('#analysisConfidence', formatConfidence(confidence));
  if (report.worker_count != null) setDiagnostic('#analysisWorkers', report.worker_count);
  if (report.native_image_pages != null) setDiagnostic('#analysisNativeImage', `${report.native_image_pages} trang`);
  if (report.ocr_calls != null) setDiagnostic('#analysisOcrCalls', `${report.ocr_calls} lần`);
}

function renderQcReport(data) {
  if (!data) return;
  $('#qcReport').textContent = JSON.stringify(data, null, 2);
}

function resetRunUi() {
  updateStageUi('QUEUED');
  setDiagnostic('#analysisRepresentation', '—');
  setDiagnostic('#analysisWatermark', '—');
  setDiagnostic('#analysisStrategy', '—');
  setDiagnostic('#analysisConfidence', '—');
  setDiagnostic('#analysisWorkers', '—');
  setDiagnostic('#analysisNativeImage', '—');
  setDiagnostic('#analysisOcrCalls', '—');
}

'''
replace_once(
    "frontend/static/app.js",
    "function setRunning(isRunning) {",
    stage_helpers + "function setRunning(isRunning) {",
)
replace_once(
    "frontend/static/app.js",
    '''  return {\n    mode: selectedMode,\n    preset: selectedPreset,''',
    '''  return {\n    mode: selectedMode,\n    content_profile: selectedContentProfile,\n    preset: selectedPreset,''',
)
replace_once(
    "frontend/static/app.js",
    '''      for (const event of (result.events || [])) handleEvent(event);\n      eventCursor = Number(result.last_event_id || eventCursor);\n      if (result.terminal) return;''',
    '''      for (const event of (result.events || [])) handleEvent(event);\n      eventCursor = Number(result.last_event_id || eventCursor);\n      if (result.stage) updateStageUi(result.stage);\n      if (result.qc_report) renderQcReport(result.qc_report);\n      if (result.terminal) return;''',
)

old_handle = r'''function handleEvent(event) {
  if (event.type === 'log') addLog(event.message, event.level);
  if (event.type === 'status') $('#statusText').textContent = event.message;
  if (event.type === 'progress') {
    let detail = 'Đang xử lý...';
    if (event.file_index && event.file_total) {
      detail = `File ${event.file_index}/${event.file_total} • Trang ${event.page_done || 0}/${event.page_total || 0}`;
    }
    setProgress(event.percent, detail, event.message);
  }
  if (event.type === 'qc') {
    addLog(event.message, event.level);
    $('#qcReport').textContent = JSON.stringify(event.summary || {}, null, 2);
  }
  if (event.type === 'done') {
    addLog(event.message, event.level);
    const cancelled = event.level === 'warning';
    setProgress(cancelled ? event.percent : 100, cancelled ? 'Đã hủy' : 'Hoàn tất', event.message);
    setRunning(false);
    lastOutputs = event.outputs || [];
    setOutputButtons(lastOutputs.length > 0);
    $('#lastOutput').textContent = lastOutputs.length
      ? `Kết quả: ${lastOutputs.length} file (${lastOutputs[lastOutputs.length - 1]})`
      : 'Kết quả: Chưa có file kết quả.';
  }
  if (event.type === 'error') {
    addLog(event.message, 'error');
    setRunning(false);
    $('#statusText').textContent = 'Có lỗi • Xem log kỹ thuật nếu cần.';
  }
}
'''
new_handle = r'''function handleEvent(event) {
  if (event.type === 'log') addLog(event.message, event.level);
  if (event.type === 'status') $('#statusText').textContent = event.message;
  if (event.type === 'stage') {
    updateStageUi(event.stage || event.message);
    renderDiagnostics(event);
    if (event.percent != null) setProgress(event.percent, $('#progressDetail').textContent, STAGE_LABELS[event.stage] || event.message);
  }
  if (event.type === 'progress') {
    let detail = 'Đang xử lý...';
    if (event.file_index && event.file_total) {
      detail = `File ${event.file_index}/${event.file_total} • Trang ${event.page_done || event.page || 0}/${event.page_total || event.pages || 0}`;
    } else if (event.page || event.pages) {
      detail = `Trang ${event.page || 0}/${event.pages || 0}`;
    }
    setProgress(event.percent, detail, event.message);
  }
  if (event.type === 'qc') {
    addLog(event.message, event.level);
    renderDiagnostics(event);
    renderQcReport(event.qc_v2 || event.summary || {});
  }
  if (event.type === 'done') {
    addLog(event.message, event.level);
    const cancelled = event.level === 'warning';
    updateStageUi(cancelled ? 'CANCELLED' : 'DONE');
    setProgress(cancelled ? event.percent : 100, cancelled ? 'Đã hủy' : 'Hoàn tất', event.message);
    setRunning(false);
    lastOutputs = event.outputs || [];
    setOutputButtons(lastOutputs.length > 0);
    $('#lastOutput').textContent = lastOutputs.length
      ? `Kết quả: ${lastOutputs.length} file (${lastOutputs[lastOutputs.length - 1]})`
      : 'Kết quả: Chưa có file kết quả.';
  }
  if (event.type === 'error') {
    addLog(event.message, 'error');
    updateStageUi('FAILED');
    setRunning(false);
    $('#statusText').textContent = 'Có lỗi • Xem log kỹ thuật nếu cần.';
  }
}
'''
replace_once("frontend/static/app.js", old_handle, new_handle)
replace_once(
    "frontend/static/app.js",
    '''  setProgress(0, 'File 0/0 • Trang 0/0', statusText);\n  setOutputButtons(false);''',
    '''  setProgress(0, 'File 0/0 • Trang 0/0', statusText);\n  resetRunUi();\n  setOutputButtons(false);''',
)
replace_once(
    "frontend/static/app.js",
    '''  modeConfigCache = cfg.modes || {};\n\n  const backendPresets''',
    '''  modeConfigCache = cfg.modes || {};\n  selectContentProfile(common.content_profile || 'auto');\n\n  const backendPresets''',
)
replace_once(
    "frontend/static/app.js",
    '''    quality_profile: selectedPreset,\n    output_grayscale:''',
    '''    quality_profile: selectedPreset,\n    content_profile: selectedContentProfile,\n    output_grayscale:''',
)
replace_once(
    "frontend/static/app.js",
    '''  $$('#modePicker .pick-card').forEach((card) => card.addEventListener('click', () => selectMode(card.dataset.mode)));\n  $$('#presetPicker .preset-card').forEach((card) => card.addEventListener('click', () => selectPreset(card.dataset.preset, true)));''',
    '''  $('#contentProfile').addEventListener('change', () => selectContentProfile($('#contentProfile').value));\n  $$('#presetPicker .preset-card').forEach((card) => card.addEventListener('click', () => selectPreset(card.dataset.preset, true)));''',
)
replace_once(
    "frontend/static/app.js",
    '''  initEvents();\n  selectMode('ly');\n  setOutputButtons(false);''',
    '''  initEvents();\n  selectContentProfile('auto');\n  resetRunUi();\n  setOutputButtons(false);''',
)

# New Auto V2 status and diagnostic components reuse the existing theme variables.
css = r'''

/* Auto V2 primary workflow */
.auto-mode-card {
  display:flex;
  align-items:center;
  gap:12px;
  margin-top:12px;
  padding:14px;
  border:1px solid var(--accent);
  border-radius:14px;
  background:var(--accent-soft);
}
.auto-mode-card > div { display:flex; flex-direction:column; gap:3px; min-width:0; }
.auto-mode-card small { color:var(--text-secondary); }
.auto-mode-badge {
  flex:0 0 auto;
  padding:6px 9px;
  border-radius:999px;
  background:var(--accent);
  color:white;
  font-size:11px;
  font-weight:800;
  letter-spacing:.06em;
}
.stage-list {
  list-style:none;
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:8px;
  margin:16px 0 0;
  padding:0;
}
.stage-list li {
  display:flex;
  align-items:center;
  gap:7px;
  min-width:0;
  padding:8px 9px;
  border:1px solid var(--border);
  border-radius:10px;
  color:var(--text-muted);
  background:var(--surface-2);
  font-size:12px;
}
.stage-list li.active { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); }
.stage-list li.done { color:var(--success); border-color:color-mix(in srgb, var(--success) 35%, var(--border)); background:var(--success-soft); }
.stage-dot {
  width:8px;
  height:8px;
  flex:0 0 auto;
  border-radius:999px;
  background:currentColor;
}
.diagnostics-grid {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:8px;
  margin-top:14px;
}
.diagnostic {
  display:flex;
  flex-direction:column;
  gap:2px;
  min-width:0;
  padding:9px 10px;
  border:1px solid var(--border);
  border-radius:10px;
  background:var(--surface-2);
}
.diagnostic span { color:var(--text-muted); font-size:11px; }
.diagnostic strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
@media (max-width:640px) {
  .stage-list { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .diagnostics-grid { grid-template-columns:1fr; }
}
'''
with Path("frontend/static/styles.css").open("a", encoding="utf-8") as handle:
    handle.write(css)
