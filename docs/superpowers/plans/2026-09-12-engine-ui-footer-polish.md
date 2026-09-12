# Engine UI and Footer Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose safe engine preferences with clear purpose copy, rename subject-oriented protection labels, add Auto/Standard/Deep footer cleanup, and eliminate visible TaiLieuOnThi footer residue without damaging legitimate footer content.

**Architecture:** Keep Auto V2 as the safety authority. Manual engine choices become compatibility-gated preferences, not unchecked direct dispatch. Add a focused native-resolution `footer_polish.py` stage after TDM-guided core cleanup, surface its metrics through `ProcessingReport`/QC, and make QC block output promotion when footer residue or protected-content damage exceeds thresholds.

**Tech Stack:** Python 3.12, PyMuPDF, NumPy, OpenCV, Pillow, pywebview, vanilla HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-engine-ui-footer-polish-design.md`

## Global Constraints

- Auto Smart remains the default processing preference.
- Manual engine preferences must run analyzer compatibility checks before processing.
- Keep content-profile wire values exactly `auto`, `math`, `physics`, `chemistry`, `ebook`.
- Do not expose Vector Repair until a production V2 strategy exists.
- Footer cleanup must be local to the trusted footer URL region; never whiten the whole footer.
- Page number and legitimate colored footer text must remain protected.
- No mandatory OCR may be introduced.
- Owner PDFs are regression/benchmark inputs only and must not be committed without explicit approval.
- Existing page-count, geometry, outside-change, watermark-residual, atomic-output and fallback guarantees remain mandatory.

---

## File structure

**Create**

- `backend/engine/raster/footer_polish.py` — footer residual scoring and native-resolution cleanup only.
- `tests/unit/test_footer_polish.py` — deterministic synthetic footer tests.

**Modify**

- `backend/engine/router/models.py` — typed engine preference.
- `backend/engine/router/router.py` — compatibility-gated preference application.
- `backend/engine/pipeline_v2/report.py` — footer/preference diagnostics in report metadata.
- `backend/engine/pipeline_v2/execute.py` — pass compatible runtime options to strategies.
- `backend/engine/raster/tdm_guided.py` — invoke native footer polish after core TDM cleanup.
- `backend/engine/strategies/raster_template.py` — carry footer cleanup option/metrics.
- `backend/engine/qc_v2/validator.py` — footer-specific QC gates.
- `backend/service.py` — normalize engine/footer settings and emit diagnostics.
- `native_api.py` — safe defaults for new payload fields.
- `frontend/index.html` — engine cards, renamed protection profiles, footer-cleanup UI and diagnostics.
- `frontend/static/app.js` — state/payload/rendering for new controls.
- `frontend/static/styles.css` — engine/profile card styling.
- `tests/unit/test_router.py`.
- `tests/unit/test_tdm_guided.py`.
- `tests/unit/test_qc_v2.py`.
- `tests/unit/test_native_api_contract.py`.
- `tests/frontend/test_frontend_contract.py`.
- `tests/integration/test_raster_template_strategy.py`.
- `tests/integration/test_end_to_end_v2.py`.

---

### Task 1: Add typed engine preference and compatibility gates

**Files:**
- Modify: `backend/engine/router/models.py`
- Modify: `backend/engine/router/router.py`
- Modify: `tests/unit/test_router.py`

**Interfaces:**
- `EnginePreference(str, Enum)` with `AUTO_SMART`, `STREAM_CLEAN`, `RASTER_CLEAN`, `COMPATIBILITY_CLEAN`.
- `build_processing_plan(profile, *, content_profile="auto", engine_preference="auto_smart") -> ProcessingPlan`.
- `ProcessingPlan.requested_engine: str = "auto_smart"`.

- [ ] **Step 1: Write failing router tests for every visible engine preference**

Add tests equivalent to:

```python
import pytest
from backend.engine.router.models import EnginePreference, StrategyKind


def test_auto_smart_preserves_router_decision(high_confidence_stream_profile):
    plan = build_processing_plan(high_confidence_stream_profile, engine_preference="auto_smart")
    assert plan.strategy is StrategyKind.STREAM_REMOVE
    assert plan.requested_engine == EnginePreference.AUTO_SMART.value


def test_stream_clean_rejects_raster_profile(raster_profile):
    with pytest.raises(ValueError, match="Stream Clean"):
        build_processing_plan(raster_profile, engine_preference="stream_clean")


def test_raster_clean_rejects_vector_profile(high_confidence_stream_profile):
    with pytest.raises(ValueError, match="Raster Clean"):
        build_processing_plan(high_confidence_stream_profile, engine_preference="raster_clean")


def test_compatibility_clean_forces_legacy(high_confidence_stream_profile):
    plan = build_processing_plan(high_confidence_stream_profile, engine_preference="compatibility_clean")
    assert plan.strategy is StrategyKind.LEGACY
```

- [ ] **Step 2: Run router tests and verify RED**

Run: `python -m pytest -q tests/unit/test_router.py`

Expected: failures because `EnginePreference`, `requested_engine`, and `engine_preference` do not exist.

- [ ] **Step 3: Implement the enum and compatibility gate**

Add to `backend/engine/router/models.py`:

```python
class EnginePreference(str, Enum):
    AUTO_SMART = "auto_smart"
    STREAM_CLEAN = "stream_clean"
    RASTER_CLEAN = "raster_clean"
    COMPATIBILITY_CLEAN = "compatibility_clean"
```

Add this defaulted field to `ProcessingPlan`:

```python
requested_engine: str = EnginePreference.AUTO_SMART.value
```

Refactor `build_processing_plan` so the existing logic first builds one `auto_plan`, then apply:

```python
preference = EnginePreference(engine_preference)

if preference is EnginePreference.COMPATIBILITY_CLEAN:
    return replace(
        auto_plan,
        strategy=StrategyKind.LEGACY,
        operations=(),
        requested_engine=preference.value,
        reason="Compatibility Clean requested explicitly",
    )
if preference is EnginePreference.STREAM_CLEAN and auto_plan.strategy is not StrategyKind.STREAM_REMOVE:
    raise ValueError("Stream Clean is incompatible with this PDF; use Auto Smart or Compatibility Clean")
if preference is EnginePreference.RASTER_CLEAN and auto_plan.strategy is not StrategyKind.RASTER_TEMPLATE:
    raise ValueError("Raster Clean is incompatible with this PDF; use Auto Smart or Compatibility Clean")
return replace(auto_plan, requested_engine=preference.value)
```

Use `dataclasses.replace`; do not duplicate confidence/strategy-selection logic.

- [ ] **Step 4: Run router-adjacent tests**

Run:

```bash
python -m pytest -q tests/unit/test_router.py tests/unit/test_analyzer_models.py tests/unit/test_raster_analyzer.py tests/unit/test_stream_analyzer.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/engine/router/models.py backend/engine/router/router.py tests/unit/test_router.py
git commit -m "feat: add compatibility-gated engine preferences"
```

---

### Task 2: Thread engine/footer preferences through the native/service pipeline

**Files:**
- Modify: `native_api.py`
- Modify: `backend/service.py`
- Modify: `backend/engine/pipeline_v2/report.py`
- Modify: `backend/engine/pipeline_v2/execute.py`
- Modify: `tests/unit/test_native_api_contract.py`
- Modify: `tests/integration/test_end_to_end_v2.py`

**Interfaces:**
- Payload fields: `engine_preference: str`, `footer_cleanup: str`.
- Allowed engine values: `auto_smart`, `stream_clean`, `raster_clean`, `compatibility_clean`.
- Allowed footer values: `auto`, `standard`, `deep`.
- `ProcessingReport.metadata` carries `requested_engine`, `footer_cleanup_level`, `footer_residual_score`, `footer_protected_change_ratio` when the selected strategy produces them.

- [ ] **Step 1: Write failing NativeApi forwarding/default tests**

Extend `tests/unit/test_native_api_contract.py` with a fake service that stores the received payload:

```python
payload = {"paths": ["x.pdf"]}
api.start_process(payload)
assert fake_service.last_payload["engine_preference"] == "auto_smart"
assert fake_service.last_payload["footer_cleanup"] == "auto"
```

Add a second case asserting explicit `raster_clean`/`deep` values are preserved.

- [ ] **Step 2: Run NativeApi tests and verify RED**

Run: `python -m pytest -q tests/unit/test_native_api_contract.py`

- [ ] **Step 3: Add payload defaults and service validation**

In `NativeApi.start_process` add:

```python
normalized.setdefault("engine_preference", "auto_smart")
normalized.setdefault("footer_cleanup", "auto")
```

In `backend/service.py` define fixed allow-sets and reject invalid values before creating the job. Pass `engine_preference` into `build_processing_plan(...)`; pass `footer_cleanup` into `execute_plan(...)` as a keyword runtime option.

- [ ] **Step 4: Keep `ProcessingReport` backward compatible through `metadata`**

Do not add mandatory dataclass constructor fields. `ProcessingReport` already has `metadata: dict[str, Any]`; store requested-engine/footer values there. Preserve existing `as_dict()` behavior.

- [ ] **Step 5: Add an E2E payload/default assertion**

Extend `tests/integration/test_end_to_end_v2.py` so default execution records `requested_engine == "auto_smart"`; an explicit compatibility request produces Legacy strategy and records `requested_engine == "compatibility_clean"`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python -m pytest -q tests/unit/test_native_api_contract.py tests/integration/test_end_to_end_v2.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add native_api.py backend/service.py backend/engine/pipeline_v2/report.py backend/engine/pipeline_v2/execute.py tests/unit/test_native_api_contract.py tests/integration/test_end_to_end_v2.py
git commit -m "feat: thread engine and footer preferences through V2"
```

---

### Task 3: Replace subject-style selector UI with engine cards and protection descriptions

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/static/app.js`
- Modify: `frontend/static/styles.css`
- Modify: `tests/frontend/test_frontend_contract.py`

**Interfaces:**
- DOM: `#enginePicker`, `[data-engine]`, `#contentProfilePicker`, `[data-content-profile]`, `#footerCleanup`.
- JS state: `selectedEnginePreference = 'auto_smart'`, `selectedContentProfile = 'auto'`, `selectedFooterCleanup = 'auto'`.
- Payload: `engine_preference`, `content_profile`, `footer_cleanup`.

- [ ] **Step 1: Rewrite the frontend contract tests first**

Require these strings in HTML:

```text
Auto Smart
Tự chọn cách xử lý nhanh và an toàn nhất cho từng PDF.
Stream Clean
Nhanh nhất — giữ nguyên chữ, vector và chất lượng PDF gốc.
Raster Clean
Dành cho PDF scan/ảnh — ưu tiên giữ chi tiết ảnh gốc.
Compatibility Clean
Cho PDF khó hoặc chưa đủ chắc chắn để dùng engine nhanh.
Formula & Diagram Safe
Diagram & Line Safe
Symbol & Structure Safe
Text & Image Safe
Footer cleanup
```

Require JS to contain all three state variables and all three payload fields. Assert `Vector Repair` is absent from elements carrying `data-engine`.

- [ ] **Step 2: Run frontend contract tests and verify RED**

Run: `python -m pytest -q tests/frontend/test_frontend_contract.py`

- [ ] **Step 3: Implement engine cards in workflow Step 3**

Use button/radio-card markup with `role="radiogroup"`; Auto Smart starts selected. Each card contains a title and one-line purpose. Maintain keyboard focus and selected state with `aria-pressed`.

- [ ] **Step 4: Replace the Advanced content-profile `<select>` with descriptive profile cards**

Use `data-content-profile="auto|math|physics|chemistry|ebook"`. User-facing labels/copy come from the spec; wire values remain unchanged.

- [ ] **Step 5: Add Footer cleanup Auto/Standard/Deep control**

Use a compact three-option control with `data-footer-cleanup="auto|standard|deep"`. Auto starts selected.

- [ ] **Step 6: Update JavaScript state and payload logic**

Add:

```javascript
let selectedEnginePreference = 'auto_smart';
let selectedFooterCleanup = 'auto';
```

Use fixed allow-lists in `selectEnginePreference`, `selectContentProfile`, and `selectFooterCleanup`. `commonPayload()` emits all three values.

- [ ] **Step 7: Update diagnostics strategy labels**

Use:

```javascript
const strategyLabels = {
  stream_remove: 'Stream Clean',
  vector_remove: 'Vector Repair',
  raster_template: 'Raster Clean',
  legacy: 'Compatibility Clean',
};
```

Vector Repair may appear only as an actual strategy diagnostic, never as a selectable card.

- [ ] **Step 8: Run syntax + contract tests**

```bash
node --check frontend/static/app.js
python -m pytest -q tests/frontend/test_frontend_contract.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/index.html frontend/static/app.js frontend/static/styles.css tests/frontend/test_frontend_contract.py
git commit -m "feat: clarify engine and protection controls"
```

---

### Task 4: Build native-resolution Footer Polish as an isolated unit

**Files:**
- Create: `backend/engine/raster/footer_polish.py`
- Create: `tests/unit/test_footer_polish.py`

**Interfaces:**

```python
from dataclasses import dataclass
from typing import Literal
import numpy as np

FooterCleanupLevel = Literal["auto", "standard", "deep"]

@dataclass(frozen=True, slots=True)
class FooterPolishConfig:
    level: FooterCleanupLevel = "auto"
    footer_url_box: tuple[float, float, float, float] = (0.32, 0.955, 0.75, 0.997)
    page_number_guard: tuple[float, float, float, float] = (0.80, 0.955, 1.00, 1.00)
    upper_content_guard_y: float = 0.945
    standard_work_dpi: int = 220
    deep_work_dpi: int = 260
    residual_threshold: float = 0.035

@dataclass(frozen=True, slots=True)
class FooterPolishMetrics:
    level_used: str
    changed_pixels: int
    residual_score_before: float
    residual_score_after: float
    protected_change_ratio: float


def score_footer_residual(
    rgb: np.ndarray,
    *,
    config: FooterPolishConfig,
    content_protect_mask: np.ndarray | None = None,
) -> float: ...


def polish_footer_residual(
    native_rgb: np.ndarray,
    *,
    config: FooterPolishConfig,
    content_protect_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, FooterPolishMetrics]: ...
```

- [ ] **Step 1: Create deterministic synthetic footer fixtures in the unit test**

Generate an off-white RGB page containing:

- saturated colored teacher/slogan strokes above normalized `y=0.945`;
- centered pale-gray glyph-like components inside `footer_url_box`;
- a thin legitimate horizontal footer rule;
- dark page-number glyph-like components inside `page_number_guard`.

Generate all data in Python/NumPy/OpenCV; add no binary fixture.

- [ ] **Step 2: Write RED scoring/protection tests**

Create a local helper in the test to convert normalized boxes to NumPy slices, capture protected regions before cleanup, and assert:

```python
assert score_footer_residual(dirty, config=cfg) > cfg.residual_threshold
page_number_before = dirty[page_number_slice].copy()
upper_before = dirty[:upper_guard_y_px].copy()
cleaned, metrics = polish_footer_residual(dirty, config=cfg)
assert metrics.residual_score_after < metrics.residual_score_before
assert metrics.residual_score_after <= cfg.residual_threshold
assert np.array_equal(cleaned[page_number_slice], page_number_before)
assert np.array_equal(cleaned[:upper_guard_y_px], upper_before)
```

For an already-clean synthetic footer assert `metrics.changed_pixels == 0` and `np.array_equal(cleaned, clean_input)`.

- [ ] **Step 3: Run test and verify RED**

Run: `python -m pytest -q tests/unit/test_footer_polish.py`

Expected: import failure because the module does not exist.

- [ ] **Step 4: Implement native-resolution ROI/guard helpers**

Convert normalized boxes to boolean masks. `effective_protect = page_number_guard | upper_content_guard | content_protect_mask`. All cleanup candidates are masked with `~effective_protect` before reconstruction.

- [ ] **Step 5: Implement residual scoring**

Inside `footer_url_box`, estimate local background from unprotected pixels using a robust median/blur. Candidate residue must be low saturation and have positive local contrast against the paper estimate. Normalize summed candidate contrast by `255 * candidate_roi_pixel_count` to produce a stable `0..1` score.

- [ ] **Step 6: Implement Standard, Deep, and Auto**

Standard uses conservative residual thresholds and one-pixel component dilation. Deep uses the same guards with stronger local-background reconstruction and at most two-pixel component dilation. Auto executes Standard, scores the result, then executes Deep only when Standard remains above `residual_threshold`.

- [ ] **Step 7: Run footer-polish tests**

Run: `python -m pytest -q tests/unit/test_footer_polish.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/engine/raster/footer_polish.py tests/unit/test_footer_polish.py
git commit -m "feat: add native footer residual polish"
```

---

### Task 5: Integrate Footer Polish into TaiLieuOnThi-guided raster repair

**Files:**
- Modify: `backend/engine/raster/tdm_guided.py`
- Modify: `backend/engine/strategies/raster_template.py`
- Modify: `tests/unit/test_tdm_guided.py`
- Modify: `tests/integration/test_raster_template_strategy.py`

**Interfaces:**

Extend `TdmGuidedResult` with defaulted fields:

```python
footer_cleanup_level: str | None = None
footer_residual_score: float | None = None
footer_protected_change_ratio: float | None = None
page_footer_residual_scores: tuple[float, ...] = ()
```

Change:

```python
clean_tailieuonthi_document(
    input_pdf,
    output_pdf,
    *,
    work_dpi=DEFAULT_WORK_DPI,
    footer_cleanup="auto",
    log=None,
    progress=None,
    should_cancel=None,
) -> TdmGuidedResult
```

- [ ] **Step 1: Add failing TDM-guided footer tests**

Monkeypatch Footer Polish in the page cleanup seam and prove `footer_cleanup="auto"` is forwarded after core cleanup. Assert document aggregation returns the max final residual/protected-change ratio and reports `deep` when any page escalates.

- [ ] **Step 2: Run RED tests**

```bash
python -m pytest -q tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py
```

- [ ] **Step 3: Call Footer Polish after native core transfer**

After `transfer_working_cleanup_to_native(...)` produces the native-size core-cleaned page, call `polish_footer_residual(...)`. Do not modify `watermaker TDM.py` for this feature.

- [ ] **Step 4: Build/forward a conservative protect mask**

Resize any available TDM debug content-protect mask to native resolution with nearest-neighbor interpolation and pass it to Footer Polish. Footer Polish must still enforce its page-number and upper-content guards when that debug mask is unavailable.

- [ ] **Step 5: Aggregate page metrics deterministically**

For the document:

```python
footer_residual_score = max(page_scores, default=0.0)
footer_protected_change_ratio = max(page_protected_ratios, default=0.0)
footer_cleanup_level = "deep" if any(level == "deep" for level in page_levels) else "standard"
```

Store per-page final scores in `page_footer_residual_scores` and strategy metadata.

- [ ] **Step 6: Forward footer cleanup through `RasterTemplateStrategy`**

Add `footer_cleanup: str = "auto"` to `RasterTemplateStrategy.execute(...)`. For marker `tailieuonthi`, pass it to `clean_tailieuonthi_document`. Copy the result metrics into `StrategyResult.metadata`.

- [ ] **Step 7: Run focused tests**

```bash
python -m pytest -q tests/unit/test_footer_polish.py tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/engine/raster/tdm_guided.py backend/engine/strategies/raster_template.py tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py
git commit -m "feat: polish TaiLieuOnThi footer residuals"
```

---

### Task 6: Make footer visual cleanliness a QC gate

**Files:**
- Modify: `backend/engine/qc_v2/validator.py`
- Modify: `tests/unit/test_qc_v2.py`

**Interfaces:**

Extend `QCReport` with defaulted optional fields:

```python
footer_residual_score: float | None = None
footer_cleanup_level: str | None = None
footer_protected_change_ratio: float | None = None
```

Extend `validate_output(...)` with:

```python
max_footer_residual_score: float = 0.035
max_footer_protected_change_ratio: float = 0.002
```

- [ ] **Step 1: Write RED QC tests**

Create metadata cases where all existing native/watermark checks pass but:

1. `footer_residual_score=0.050` fails;
2. `footer_protected_change_ratio=0.010` fails;
3. `footer_residual_score=0.020` and `footer_protected_change_ratio=0.0` pass.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/unit/test_qc_v2.py`

- [ ] **Step 3: Apply footer gates only when footer metadata exists**

When `footer_residual_score` is present, append a reason and fail if it exceeds `max_footer_residual_score`. When `footer_protected_change_ratio` is present, append a reason and fail if it exceeds `max_footer_protected_change_ratio`.

- [ ] **Step 4: Preserve non-TDM behavior**

When footer metrics are absent, Stream Remove, generic raster, and Legacy paths retain current QC semantics exactly.

- [ ] **Step 5: Run QC + E2E tests**

```bash
python -m pytest -q tests/unit/test_qc_v2.py tests/integration/test_end_to_end_v2.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/engine/qc_v2/validator.py tests/unit/test_qc_v2.py
git commit -m "feat: gate TaiLieuOnThi footer visual quality"
```

---

### Task 7: Add end-to-end footer diagnostics and regression coverage

**Files:**
- Modify: `tests/integration/test_end_to_end_v2.py`
- Modify: `tests/frontend/test_frontend_contract.py`
- Modify: `frontend/static/app.js`
- Modify: `frontend/index.html`

**Interfaces:**
- Report/event diagnostics: `requested_engine`, `footer_cleanup_level`, `footer_residual_score`, `footer_protected_change_ratio`.
- DOM diagnostics: `#analysisFooterCleanup`, `#analysisFooterResidual`.

- [ ] **Step 1: Add an in-test synthetic raster footer fixture**

Generate a full-page image PDF inside the test with a centered gray footer URL-like watermark, colored protected footer text, page number, and footer rule. Route it through the known TaiLieuOnThi strategy seam without adding owner PDFs to git.

- [ ] **Step 2: Assert E2E output properties**

Require:

```python
assert output_doc.page_count == input_doc.page_count
assert report.metadata["footer_residual_score"] <= 0.035
assert report.metadata["footer_protected_change_ratio"] <= 0.002
assert report.metadata["footer_cleanup_level"] in {"standard", "deep"}
assert report.ocr_calls == 0
```

Compare synthetic page-number and colored-content guard pixels before/after within an exact or explicitly documented one-level image tolerance.

- [ ] **Step 3: Render footer diagnostics in UI**

Add the two diagnostics fields to HTML. In JS, render `footer_cleanup_level` as `Standard`/`Deep` and `footer_residual_score` as a percentage with one decimal place. Render `—` when metrics do not apply.

- [ ] **Step 4: Run frontend + E2E tests**

```bash
node --check frontend/static/app.js
python -m pytest -q tests/frontend/test_frontend_contract.py tests/integration/test_end_to_end_v2.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_end_to_end_v2.py tests/frontend/test_frontend_contract.py frontend/index.html frontend/static/app.js
git commit -m "test: cover footer polish end to end"
```

---

### Task 8: Owner-PDF visual regression, full-stack gate, and documentation

**Files:**
- Modify: `README.md`
- Do not add owner PDF binaries.

- [ ] **Step 1: Run the full automated suite**

```bash
python -m compileall -q backend desktop_app.py native_api.py
node --check frontend/static/app.js
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Process owner regression PDFs**

Run Auto Smart + Balanced + Footer cleanup Auto against the supplied TaiLieuOnThi inputs, including the Live 1 file and the Live 2 original/backup source. Record actual strategy, cleanup level, footer residual, watermark residual, outside-change ratio, total time and output size.

- [ ] **Step 3: Inspect representative pages at 100% and 200% zoom**

Inspect first, second, one dense middle page and the final page. Acceptance is all-or-nothing:

- no readable or obvious footer URL ghost;
- no rectangular white patch, halo edge, or tone seam;
- page number visually unchanged;
- colored teacher/slogan line visually unchanged;
- diagonal watermark cleanup does not regress.

Any failure blocks merge and returns implementation to Task 4/5.

- [ ] **Step 4: Run Windows/native and Chromium full-stack smoke**

Verify pywebview window construction/NativeApi binding, frontend primary flow, Auto Smart default, Footer cleanup Auto default, processing through DONE, actual-engine diagnostics, and footer metrics.

- [ ] **Step 5: Update README**

Document Engine preference vs Content protection profile, Footer cleanup Auto/Standard/Deep, compatibility-gate behavior, and Auto Smart as the recommended default.

- [ ] **Step 6: Run final verification and commit docs**

Run the full automated suite again, then:

```bash
git add README.md
git commit -m "docs: explain engine preferences and footer cleanup"
```

**Merge gate:** automated suite green + Windows/native smoke green + Chromium smoke green + owner visual regressions accepted.
