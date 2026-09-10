const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let selectedLocalFiles = [];
let selectedMode = 'auto';
let selectedContentProfile = 'auto';
let selectedPreset = 'balanced';
let currentJobId = null;
let eventCursor = 0;
let pollingGeneration = 0;
let configCache = null;
let modeConfigCache = {};
let lastOutputs = [];
let presetValues = {
  fast: { hint: 'Tốc độ cao, chất lượng vừa.', dpi: 200, output_dpi: 200, quality: 88, cpu: 0 },
  balanced: { hint: 'Cân bằng giữa tốc độ và chất lượng.', dpi: 240, output_dpi: 240, quality: 92, cpu: 0 },
  high_quality: { hint: 'Chất lượng cao nhất, chậm hơn.', dpi: 320, output_dpi: 320, quality: 95, cpu: 0 },
  safe_mode: { hint: 'Thiết lập bảo thủ, chạy một worker.', dpi: 240, output_dpi: 240, quality: 95, cpu: 1 },
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function nativeCall(method, ...args) {
  if (!window.pywebview?.api) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Cầu nối Python chưa sẵn sàng.')), 8000);
      window.addEventListener('pywebviewready', () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
    });
  }
  const fn = window.pywebview?.api?.[method];
  if (typeof fn !== 'function') throw new Error(`Thiếu API native: ${method}`);
  return fn(...args);
}

function escapeHtml(str) {
  return String(str || '').replace(/[&<>'"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
}

function formatSize(bytes) {
  const n = Number(bytes || 0);
  if (n <= 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function addLog(message, level = 'info') {
  const log = $('#logConsole');
  const line = document.createElement('div');
  const time = new Date().toLocaleTimeString('vi-VN', { hour12: false });
  line.className = `log-line ${level}`;
  line.innerHTML = `<span class="time">[${time}]</span> ${escapeHtml(String(message || ''))}`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function setProgress(percent, detail, status) {
  const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
  $('#progressBar').style.width = `${clamped}%`;
  $('.progress-wrap').setAttribute('aria-valuenow', String(Math.round(clamped)));
  if (detail) $('#progressDetail').textContent = detail;
  if (status) $('#statusText').textContent = status;
}


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

function setRunning(isRunning) {
  const startBtn = $('#startBtn');
  startBtn.disabled = isRunning;
  $('#cancelBtn').disabled = !isRunning;
  startBtn.textContent = isRunning ? 'Đang xử lý...' : (startBtn.dataset.idleLabel || '▶ Xử lý PDF');
}

function setOutputButtons(enabled) {
  $('#openFileBtn').disabled = !enabled;
  $('#openFolderBtn').disabled = !enabled;
}

function updateStartActionUi() {
  const startBtn = $('#startBtn');
  const hint = $('#runSafetyHint');
  const overwrite = $('#overwrite')?.checked;
  const label = overwrite ? '⚠ Xử lý & ghi đè file gốc' : '▶ Xử lý PDF';
  hint.className = overwrite ? 'callout warning' : 'callout info';
  hint.textContent = overwrite
    ? 'Đang bật ghi đè. App xử lý vào file tạm, chạy QC, backup vào _backup rồi mới thay file gốc.'
    : 'Chế độ an toàn: tạo file mới với hậu tố _clean, không đụng file gốc.';
  startBtn.dataset.idleLabel = label;
  if (!startBtn.disabled) startBtn.textContent = label;
}

function parseLocalPathLines() {
  return ($('#localPathInput')?.value || '')
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^"|"$/g, ''))
    .filter(Boolean);
}

function getLocalFileItems() {
  const byPath = new Map();
  selectedLocalFiles.forEach((item) => {
    if (item.path) byPath.set(String(item.path), item);
  });
  parseLocalPathLines().forEach((path) => {
    if (!byPath.has(path)) {
      const name = path.split(/[\\/]/).pop() || path;
      byPath.set(path, { path, name, size: 0, parent_dir: path.replace(/[\\/][^\\/]*$/, '') });
    }
  });
  return Array.from(byPath.values());
}

function getLocalPaths() {
  const seen = new Set();
  const paths = [];
  getLocalFileItems().forEach((item) => {
    const path = String(item.path || '').trim();
    const key = path.toLocaleLowerCase();
    if (path && !seen.has(key)) {
      seen.add(key);
      paths.push(path);
    }
  });
  return paths;
}

function renderFileList() {
  const list = $('#fileList');
  const items = getLocalFileItems();
  $('#fileCount').textContent = `${items.length} file PDF đã chọn`;
  if (items.length === 0) {
    list.className = 'file-list empty';
    list.textContent = 'Chưa có file. Bấm “Chọn file trên máy” hoặc nhập đường dẫn PDF.';
    return;
  }
  list.className = 'file-list';
  list.innerHTML = '';
  items.forEach((item, index) => {
    const row = document.createElement('div');
    row.className = 'file-row';
    const size = formatSize(item.size);
    row.innerHTML = `
      <div class="file-meta">
        <span class="file-name">${index + 1}. ${escapeHtml(item.name || item.path)}</span>
        <span class="file-path">${escapeHtml(item.path)}${size ? ` • ${size}` : ''}</span>
      </div>
      <button class="remove-btn" title="Bỏ file" data-path="${escapeHtml(item.path)}">×</button>
    `;
    list.appendChild(row);
  });
  $$('.remove-btn').forEach((btn) => {
    btn.addEventListener('click', () => removeLocalPath(btn.dataset.path));
  });
}

function removeLocalPath(path) {
  selectedLocalFiles = selectedLocalFiles.filter((item) => item.path !== path);
  const lines = parseLocalPathLines().filter((line) => line !== path);
  $('#localPathInput').value = lines.join('\n');
  renderFileList();
}

function addLocalFiles(files) {
  const incoming = (files || []).filter((item) => item && item.path);
  if (!incoming.length) {
    renderFileList();
    return;
  }
  const byPath = new Map(getLocalFileItems().map((item) => [String(item.path).toLocaleLowerCase(), item]));
  incoming.forEach((item) => byPath.set(String(item.path).toLocaleLowerCase(), item));
  selectedLocalFiles = Array.from(byPath.values());
  $('#localPathInput').value = selectedLocalFiles.map((item) => item.path).join('\n');
  renderFileList();
  addLog(`Đã chọn ${selectedLocalFiles.length} file PDF.`, 'success');
}

function toggleOutputState() {
  const sameFolder = $('#sameFolder').checked;
  const overwrite = $('#overwrite').checked;
  $('#outputDir').disabled = sameFolder || overwrite;
  $('#chooseOutputDir').disabled = sameFolder || overwrite;
  $('#suffix').disabled = overwrite;
  $('#overwriteWarning').classList.toggle('hidden', !overwrite);
  updateStartActionUi();
}

const CONTENT_PROFILES = new Set(['auto', 'math', 'physics', 'chemistry', 'ebook']);

function selectContentProfile(profile) {
  selectedContentProfile = CONTENT_PROFILES.has(profile) ? profile : 'auto';
  const select = $('#contentProfile');
  if (select) select.value = selectedContentProfile;
}

function selectPreset(preset, applyValues = true) {
  selectedPreset = presetValues[preset] ? preset : 'balanced';
  $$('#presetPicker .preset-card').forEach((card) => card.classList.toggle('selected', card.dataset.preset === selectedPreset));
  const values = presetValues[selectedPreset];
  $('#presetHint').textContent = values?.hint || '';
  if (applyValues && values) {
    $('#dpiSetting').value = values.dpi;
    $('#outputDpiSetting').value = values.output_dpi;
    $('#jpegQualitySetting').value = values.quality;
    $('#jpegQualityOut').textContent = String(values.quality);
  }
}

function commonPayload() {
  return {
    mode: selectedMode,
    content_profile: selectedContentProfile,
    preset: selectedPreset,
    same_folder: $('#sameFolder').checked,
    overwrite: $('#overwrite').checked,
    suffix: $('#suffix').value || '_clean',
    output_dir: $('#outputDir').value || '',
    output_grayscale: $('#outputGrayscale').checked,
    ly_aggressive_white: !!modeConfigCache.ly?.aggressive_white,
    dpi: Number($('#dpiSetting').value || presetValues[selectedPreset].dpi),
    output_dpi: Number($('#outputDpiSetting').value || presetValues[selectedPreset].output_dpi),
    quality: Number($('#jpegQualitySetting').value || presetValues[selectedPreset].quality),
  };
}

async function connectEvents(jobId) {
  const generation = ++pollingGeneration;
  eventCursor = 0;
  while (generation === pollingGeneration && currentJobId === jobId) {
    try {
      const result = await nativeCall('poll_job', jobId, eventCursor);
      for (const event of (result.events || [])) handleEvent(event);
      eventCursor = Number(result.last_event_id || eventCursor);
      if (result.stage) updateStageUi(result.stage);
      if (result.qc_report) renderQcReport(result.qc_report);
      if (result.terminal) return;
    } catch (err) {
      addLog(`Không đọc được tiến độ: ${err.message}`, 'warning');
      setRunning(false);
      return;
    }
    await sleep(180);
  }
}

function handleEvent(event) {
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

function prepareBeforeStart(statusText) {
  pollingGeneration += 1;
  currentJobId = null;
  $('#logConsole').innerHTML = '';
  setProgress(0, 'File 0/0 • Trang 0/0', statusText);
  resetRunUi();
  setOutputButtons(false);
  setRunning(true);
}

async function startProcessing() {
  const paths = getLocalPaths();
  if (!paths.length) {
    addLog('Hãy chọn ít nhất một file PDF hoặc nhập đường dẫn PDF thật.', 'warning');
    return;
  }
  if ($('#overwrite').checked) {
    const ok = confirm('Bạn đang bật ghi đè file gốc. App sẽ backup vào thư mục _backup trước khi thay thế. Tiếp tục?');
    if (!ok) return;
  }

  prepareBeforeStart('Đang tạo tác vụ...');
  try {
    const data = await nativeCall('start_process', { ...commonPayload(), paths });
    currentJobId = data.job_id;
    addLog(`Đã tạo tác vụ ${currentJobId}.`, 'success');
    void connectEvents(currentJobId);
  } catch (err) {
    addLog(`Không thể bắt đầu xử lý: ${err.message}`, 'error');
    setRunning(false);
  }
}

async function cancelProcessing() {
  if (!currentJobId) return;
  try {
    await nativeCall('cancel_job', currentJobId);
  } catch (err) {
    addLog(`Không thể hủy: ${err.message}`, 'error');
  }
}

async function callNative(method, args, okMessage) {
  try {
    const data = await nativeCall(method, ...(args || []));
    addLog(okMessage || data.path || 'OK', 'success');
  } catch (err) {
    addLog(err.message, 'error');
  }
}

async function chooseLocalFiles() {
  try {
    const data = await nativeCall('choose_local_files');
    addLocalFiles(data.files || []);
  } catch (err) {
    addLog(`Không mở được hộp thoại chọn file: ${err.message}`, 'error');
  }
}

async function chooseOutputDir() {
  try {
    const data = await nativeCall('choose_output_dir');
    if (data.path) {
      $('#outputDir').value = data.path;
      addLog(`Đã chọn thư mục lưu: ${data.path}`, 'success');
    }
  } catch (err) {
    addLog(`Không mở được hộp thoại chọn thư mục: ${err.message}`, 'error');
  }
}

function loadModeFields(mode) {
  const cfg = JSON.parse(JSON.stringify(modeConfigCache[mode] || {}));
  $('#settingsMode').value = mode;
  $('#headerHeight').value = cfg.header_height ?? '';
  $('#footerHeight').value = cfg.footer_height ?? '';
  $('#graphSafe').checked = !!(cfg.graph_safe_paper_mode || mode === 'toan');
  $('#graphSafe').disabled = mode === 'toan';
  $('#lyAggressiveRow').classList.toggle('hidden', mode !== 'ly');
  $('#lyAggressive').checked = !!cfg.aggressive_white;

  delete cfg.display_name;
  delete cfg.header_height;
  delete cfg.footer_height;
  delete cfg.graph_safe_paper_mode;
  delete cfg.aggressive_white;
  $('#extraJson').value = JSON.stringify(cfg, null, 2);
}

async function loadConfig() {
  configCache = await nativeCall('get_config');
  const cfg = configCache.config || {};
  const common = cfg.common || {};
  modeConfigCache = cfg.modes || {};
  selectContentProfile(common.content_profile || 'auto');

  const backendPresets = configCache.presets || {};
  for (const [name, values] of Object.entries(backendPresets)) {
    presetValues[name] = { ...presetValues[name], ...values };
  }
  selectPreset(common.quality_profile || 'balanced', false);

  $('#popplerPath').value = common.poppler_path || '';
  $('#dpiSetting').value = common.dpi ?? presetValues[selectedPreset].dpi;
  $('#outputDpiSetting').value = common.output_pdf_dpi ?? common.output_dpi ?? presetValues[selectedPreset].output_dpi;
  $('#jpegQualitySetting').value = common.jpeg_quality ?? presetValues[selectedPreset].quality;
  $('#jpegQualityOut').textContent = $('#jpegQualitySetting').value;
  $('#outputGrayscale').checked = common.output_grayscale ?? false;

  $('#sameFolder').checked = common.same_folder ?? true;
  $('#overwrite').checked = common.overwrite ?? false;
  $('#suffix').value = common.suffix || '_clean';
  $('#outputDir').value = common.output_dir || '';
  toggleOutputState();

  const speed = cfg.speed || {};
  $('#fastLosslessPng').checked = speed.fast_lossless_png ?? true;
  $('#skipPreredactNoText').checked = speed.skip_preredact_when_no_text ?? true;
  $('#batchPageLogs').checked = speed.batch_page_logs ?? true;
  $('#logEveryPages').value = speed.log_every_pages ?? 5;

  const qc = cfg.qc || {};
  $('#qcMode').value = qc.mode || 'full';
  $('#qcDpi').value = qc.dpi ?? 150;
  $('#qcQuickMaxPages').value = qc.quick_max_pages ?? 12;
  $('#failOnVisualLoss').checked = qc.fail_on_visual_loss ?? false;
  $('#darkLostMax').value = qc.dark_lost_ratio_max ?? 0.025;
  $('#contrastLostMax').value = qc.high_contrast_lost_ratio_max ?? 0.025;
  $('#meanAbsDiffMax').value = qc.mean_abs_diff_max ?? 'null';

  loadModeFields($('#settingsMode').value || 'ly');
}

function buildCommonSettingsPayload() {
  return {
    poppler_path: $('#popplerPath').value || '',
    dpi: Number($('#dpiSetting').value || 240),
    output_pdf_dpi: Number($('#outputDpiSetting').value || 240),
    jpeg_quality: Number($('#jpegQualitySetting').value || 92),
    quality_profile: selectedPreset,
    content_profile: selectedContentProfile,
    output_grayscale: $('#outputGrayscale').checked,
    same_folder: $('#sameFolder').checked,
    overwrite: $('#overwrite').checked,
    suffix: $('#suffix').value || '_clean',
    output_dir: $('#outputDir').value || '',
  };
}

async function saveOutputDefaults() {
  const common = {
    same_folder: $('#sameFolder').checked,
    overwrite: $('#overwrite').checked,
    suffix: $('#suffix').value || '_clean',
    output_dir: $('#outputDir').value || '',
  };
  try {
    await nativeCall('save_settings', { common });
    await loadConfig();
    addLog('Đã lưu tùy chọn nơi lưu.', 'success');
  } catch (err) {
    addLog(err.message, 'error');
  }
}

async function saveSettings() {
  let extra = {};
  try {
    extra = JSON.parse($('#extraJson').value || '{}');
  } catch (err) {
    addLog(`JSON mode không hợp lệ: ${err.message}`, 'error');
    return;
  }
  const mode = $('#settingsMode').value;
  const modeValues = {
    ...extra,
    header_height: $('#headerHeight').value || null,
    footer_height: $('#footerHeight').value || null,
    graph_safe_paper_mode: $('#graphSafe').checked || mode === 'toan',
  };
  if (mode === 'ly') modeValues.aggressive_white = $('#lyAggressive').checked;

  const rawMeanDiff = String($('#meanAbsDiffMax').value || '').trim();
  const payload = {
    common: buildCommonSettingsPayload(),
    mode,
    mode_values: modeValues,
    qc: {
      mode: $('#qcMode').value || 'full',
      dpi: Number($('#qcDpi').value || 150),
      quick_max_pages: Number($('#qcQuickMaxPages').value || 12),
      fail_on_visual_loss: $('#failOnVisualLoss').checked,
      dark_lost_ratio_max: Number($('#darkLostMax').value || 0.025),
      high_contrast_lost_ratio_max: Number($('#contrastLostMax').value || 0.025),
      mean_abs_diff_max: rawMeanDiff && !['null', 'none'].includes(rawMeanDiff.toLowerCase()) ? Number(rawMeanDiff) : null,
    },
    speed: {
      fast_lossless_png: $('#fastLosslessPng').checked,
      skip_preredact_when_no_text: $('#skipPreredactNoText').checked,
      batch_page_logs: $('#batchPageLogs').checked,
      log_every_pages: Number($('#logEveryPages').value || 5),
    },
  };
  if (Number.isNaN(payload.qc.mean_abs_diff_max)) {
    addLog('Sai số trung bình tuyệt đối phải là số hoặc null.', 'error');
    return;
  }

  try {
    await nativeCall('save_settings', payload);
    await loadConfig();
    addLog('Đã lưu cấu hình mặc định.', 'success');
  } catch (err) {
    addLog(err.message, 'error');
  }
}

async function resetSettings() {
  if (!confirm('Khôi phục cấu hình mặc định?')) return;
  try {
    await nativeCall('reset_settings');
    await loadConfig();
    addLog('Đã reset cấu hình mặc định.', 'success');
  } catch (err) {
    addLog(err.message, 'error');
  }
}

function initTabs() {
  $$('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      $$('.tab').forEach((t) => t.classList.remove('active'));
      $$('.tab-panel').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      $(`#${tab.dataset.tab}`).classList.add('active');
    });
  });
}

function initTheme() {
  const saved = localStorage.getItem('pdf-cleaner-theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'light');
  document.documentElement.classList.toggle('dark', theme === 'dark');
  $('#themeToggle').textContent = theme === 'dark' ? '☀' : '☾';
  $('#themeToggle').addEventListener('click', () => {
    const nextDark = !document.documentElement.classList.contains('dark');
    document.documentElement.classList.toggle('dark', nextDark);
    localStorage.setItem('pdf-cleaner-theme', nextDark ? 'dark' : 'light');
    $('#themeToggle').textContent = nextDark ? '☀' : '☾';
  });
}

function initEvents() {
  $('#chooseLocalFiles').addEventListener('click', chooseLocalFiles);
  $('#localPathInput').addEventListener('input', renderFileList);
  $('#clearFiles').addEventListener('click', () => {
    selectedLocalFiles = [];
    $('#localPathInput').value = '';
    renderFileList();
  });

  $('#sameFolder').addEventListener('change', toggleOutputState);
  $('#overwrite').addEventListener('change', toggleOutputState);
  $('#chooseOutputDir').addEventListener('click', chooseOutputDir);
  $('#saveOutputDefaults').addEventListener('click', saveOutputDefaults);

  $('#contentProfile').addEventListener('change', () => selectContentProfile($('#contentProfile').value));
  $$('#presetPicker .preset-card').forEach((card) => card.addEventListener('click', () => selectPreset(card.dataset.preset, true)));

  $('#startBtn').addEventListener('click', startProcessing);
  $('#cancelBtn').addEventListener('click', cancelProcessing);
  $('#openFileBtn').addEventListener('click', () => currentJobId && callNative('open_latest_file', [currentJobId], 'Đã mở file kết quả.'));
  $('#openFolderBtn').addEventListener('click', () => currentJobId && callNative('open_latest_folder', [currentJobId], 'Đã mở thư mục kết quả.'));
  $('#clearLog').addEventListener('click', () => { $('#logConsole').innerHTML = ''; });
  $('#openDevLog').addEventListener('click', () => callNative('open_log', [], 'Đã mở log kỹ thuật.'));
  $('#openToolDir').addEventListener('click', () => callNative('open_tool_dir', [], 'Đã mở thư mục app.'));

  $('#jpegQualitySetting').addEventListener('input', () => { $('#jpegQualityOut').textContent = $('#jpegQualitySetting').value; });
  $('#settingsMode').addEventListener('change', () => loadModeFields($('#settingsMode').value));
  $('#saveSettings').addEventListener('click', saveSettings);
  $('#reloadSettings').addEventListener('click', loadConfig);
  $('#resetSettings').addEventListener('click', resetSettings);
}

document.addEventListener('DOMContentLoaded', async () => {
  initTabs();
  initTheme();
  initEvents();
  selectContentProfile('auto');
  resetRunUi();
  setOutputButtons(false);
  renderFileList();
  try {
    await loadConfig();
    addLog('PDF_Cleaner Native sẵn sàng — desktop-only, không server.', 'success');
  } catch (err) {
    addLog(`Không đọc được cấu hình Python: ${err.message}`, 'error');
  }
});
