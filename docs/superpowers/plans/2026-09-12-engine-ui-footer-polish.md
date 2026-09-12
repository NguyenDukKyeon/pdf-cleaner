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
- `backend/engine/pipeline_v2/report.py` — footer and preference diagnostics.
- `backend/engine/pipeline_v2/execute.py` — pass compatible runtime options to strategies and reports.
- `backend/engine/raster/tdm_guided.py` — invoke native footer polish after core TDM cleanup.
- `backend/engine/strategies/raster_template.py` — carry footer cleanup option/metrics.
- `backend/engine/qc_v2/validator.py` — footer-specific QC gates.
- `backend/service.py` — normalize engine/footer settings and emit diagnostics.
- `native_api.py` — safe defaults for new payload fields.
- `frontend/index.html` — engine cards, renamed protection profiles, footer-cleanup UI and diagnostics.
- `frontend/static/app.js` — state/payload/rendering for new controls.
- `frontend/static/styles.css` — engine/profile card styling.
- `tests/unit/test_router.py`
- `tests/unit/test_tdm_guided.py`
- `tests/unit/test_qc_v2.py`
- `tests/unit/test_native_api_contract.py`
- `tests/frontend/test_frontend_contract.py`
- `tests/integration/test_end_to_end_v2.py`

---

### Task 1: Add typed engine preference and compatibility gates

**Files:**
- Modify: `backend/engine/router/models.py`
- Modify: `backend/engine/router/router.py`
- Test: `tests/unit/test_router.py`

**Interfaces:**
- Produces: `EnginePreference(str, Enum)` with `AUTO_SMART`, `STREAM_CLEAN`, `RASTER_CLEAN`, `COMPATIBILITY_CLEAN`.
- Produces: `build_processing_plan(profile, *, content_profile="auto", engine_preference="auto_smart") -> ProcessingPlan`.
- Produces: `ProcessingPlan.requested_engine: str = "auto_smart"`.

- [ ] **Step 1: Write failing router tests for each preference**

Add tests equivalent to:

```python
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

Add to `router/models.py`:

```python
class EnginePreference(str, Enum):
    AUTO_SMART = "auto_smart"
    STREAM_CLEAN = "stream_clean"
    RASTER_CLEAN = "raster_clean"
    COMPATIBILITY_CLEAN = "compatibility_clean"
```

Add `requested_engine: str = EnginePreference.AUTO_SMART.value` to `ProcessingPlan`.

In `router.py`, normalize the preference first. Build the normal Auto plan using existing confidence logic, then apply:

```python
if preference is EnginePreference.COMPATIBILITY_CLEAN:
    return replace(auto_plan, strategy=StrategyKind.LEGACY, operations=(), requested_engine=preference.value,
                   reason="Compatibility Clean requested explicitly")
if preference is EnginePreference.STREAM_CLEAN and auto_plan.strategy is not StrategyKind.STREAM_REMOVE:
    raise ValueError("Stream Clean is incompatible with this PDF; use Auto Smart or Compatibility Clean")
if preference is EnginePreference.RASTER_CLEAN and auto_plan.strategy is not StrategyKind.RASTER_TEMPLATE:
    raise ValueError("Raster Clean is incompatible with this PDF; use Auto Smart or Compatibility Clean")
return replace(auto_plan, requested_engine=preference.value)
```

Use `dataclasses.replace`; do not duplicate router confidence logic.

- [ ] **Step 4: Run router tests and full router-adjacent unit tests**

Run: `python -m pytest -q tests/unit/test_router.py tests/unit/test_analyzer_models.py tests/unit/test_raster_analyzer.py tests/unit/test_stream_analyzer.py`

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
- Test: `tests/unit/test_native_api_contract.py`
- Test: existing service/integration tests that exercise `start_process_local`

**Interfaces:**
- Payload fields: `engine_preference: str`, `footer_cleanup: str`.
- Allowed engine values: `auto_smart`, `stream_clean`, `raster_clean`, `compatibility_clean`.
- Allowed footer values: `auto`, `standard`, `deep`.
- `ProcessingReport.metadata` carries `requested_engine`, `footer_cleanup_level`, `footer_residual_score`, `footer_protected_change_ratio` when available.

- [ ] **Step 1: Write failing NativeApi default/forwarding tests**

Extend `tests/unit/test_native_api_contract.py` with a fake service and assert:

```python
payload = {"paths": ["x.pdf"]}
api.start_process(payload)
assert fake_service.last_payload["engine_preference"] == "auto_smart"
assert fake_service.last_payload["footer_cleanup"] == "auto"
```

Also assert explicit values are preserved.

- [ ] **Step 2: Run NativeApi tests and verify RED**

Run: `python -m pytest -q tests/unit/test_native_api_contract.py`

- [ ] **Step 3: Add payload defaults and validation**

In `NativeApi.start_process`:

```python
normalized.setdefault("engine_preference", "auto_smart")
normalized.setdefault("footer_cleanup", "auto")
```

In `backend/service.py`, validate against constant sets before starting a job. Invalid values raise `ValueError` with the field name and supplied value. Pass `engine_preference` into `build_processing_plan(...)` and pass `footer_cleanup` into execution runtime options.

- [ ] **Step 4: Extend `ProcessingReport` diagnostics without breaking callers**

Keep existing report fields. Add optional metadata values rather than mandatory constructor parameters unless the current report dataclass already has a suitable optional field. The serialized report must remain backward compatible.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest -q tests/unit/test_native_api_contract.py tests/integration/test_end_to_end_v2.py`

Expected: PASS.

- [ ] **Step 6: Commit**

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
- Test: `tests/frontend/test_frontend_contract.py`

**Interfaces:**
- DOM: `#enginePicker`, `[data-engine]`, `#contentProfilePicker`, `[data-content-profile]`, `#footerCleanup`.
- JS state: `selectedEnginePreference = 'auto_smart'`, `selectedContentProfile = 'auto'`, `selectedFooterCleanup = 'auto'`.
- Payload: `engine_preference`, `content_profile`, `footer_cleanup`.

- [ ] **Step 1: Rewrite the frontend contract tests first**

Require these labels/copy in HTML:

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

Require JS to contain the three new state variables and payload fields. Require that `Vector Repair` is absent from selectable engine markup.

- [ ] **Step 2: Run frontend contract tests and verify RED**

Run: `python -m pytest -q tests/frontend/test_frontend_contract.py`

- [ ] **Step 3: Implement engine cards in Step 3 of the main workflow**

Use button/radio-card markup, not a native `<select>`. Auto Smart is selected initially. Each card contains `<b>` title plus `<small>` one-line purpose. Add accessible `role="radiogroup"` and selected state via `aria-pressed` or equivalent.

- [ ] **Step 4: Replace the Advanced content-profile `<select>` with descriptive profile cards**

Keep the wire values on `data-content-profile` exactly unchanged. Only labels/copy change.

- [ ] **Step 5: Add Footer cleanup control**

Use a compact three-option segmented/card control for `auto`, `standard`, `deep`, with Auto selected by default and the copy from the spec.

- [ ] **Step 6: Update JavaScript selection and payload logic**

Add:

```javascript
let selectedEnginePreference = 'auto_smart';
let selectedFooterCleanup = 'auto';
```

Implement `selectEnginePreference`, `selectContentProfile`, `selectFooterCleanup` using fixed allow-lists. `commonPayload()` must emit all three values.

- [ ] **Step 7: Update diagnostics labels**

Map actual strategies to user-facing names:

```javascript
const strategyLabels = {
  stream_remove: 'Stream Clean',
  vector_remove: 'Vector Repair',
  raster_template: 'Raster Clean',
  legacy: 'Compatibility Clean',
};
```

Display Vector Repair only as a diagnostic result, never as a selectable engine card.

- [ ] **Step 8: Run frontend syntax and contract tests**

Run:

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
FooterCleanupLevel = Literal["auto", "standard", "deep"]
FooterPolishConfig(...)
FooterPolishMetrics(...)
score_footer_residual(rgb, *, config, content_protect_mask=None) -> float
polish_footer_residual(native_rgb, *, config, content_protect_mask=None) -> tuple[np.ndarray, FooterPolishMetrics]
```

- [ ] **Step 1: Create deterministic synthetic footer fixtures in the unit test**

Generate an RGB white/off-white page with:

- colored teacher/slogan text represented by saturated rectangles/strokes above `y=0.945`;
- centered pale gray glyph-like components in the footer URL ROI;
- a thin horizontal footer rule;
- dark page-number glyph-like components inside the right guard.

Do this in Python/NumPy/OpenCV inside the test; do not add binary fixtures.

- [ ] **Step 2: Write RED tests for scoring and protection**

Tests must prove:

```python
assert score_footer_residual(dirty, config=cfg) > cfg.residual_threshold
cleaned, metrics = polish_footer_residual(dirty, config=cfg)
assert metrics.residual_score_after < metrics.residual_score_before
assert metrics.residual_score_after <= cfg.residual_threshold
assert np.array_equal(cleaned[page_number_guard], dirty[page_number_guard])
assert np.array_equal(cleaned[upper_content_guard], dirty[upper_content_guard])
```

Also test that an already clean footer changes zero or near-zero pixels.

- [ ] **Step 3: Run test and verify RED**

Run: `python -m pytest -q tests/unit/test_footer_polish.py`

Expected: import/module failure.

- [ ] **Step 4: Implement ROI/guard helpers**

Implement normalized-box-to-pixel conversion, page-number guard, upper-content guard, and optional external content-protect mask. All masks are native-resolution booleans.

- [ ] **Step 5: Implement residual scoring**

Within the footer URL ROI, estimate local background with a robust blur/median from unprotected pixels. Score only low-saturation residual candidate pixels. Normalize residual contrast by ROI size so the score is comparable across native resolutions.

Use one function for both before/after scoring to avoid metric drift.

- [ ] **Step 6: Implement Standard and Deep cleanup**

Standard uses conservative candidate thresholds and minimal component dilation. Deep may slightly expand candidate components and use stronger background reconstruction, but both share exactly the same content/page-number guards.

For `level="auto"`, execute Standard, score, and run Deep only if the Standard score remains above threshold.

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
- Test: `tests/unit/test_tdm_guided.py`
- Test: `tests/integration/test_raster_template_strategy.py`

**Interfaces:**
- `clean_tailieuonthi_document(..., footer_cleanup: str = "auto") -> TdmGuidedResult`.
- Extend `TdmGuidedResult` with optional/defaulted footer metrics so existing constructors/tests remain compatible.

- [ ] **Step 1: Add failing TDM-guided tests**

Add a unit test around the page cleanup path using monkeypatch/fake images to prove `footer_cleanup="auto"` invokes Footer Polish after core cleanup and returns:

```python
result.footer_cleanup_level in {"standard", "deep"}
result.footer_residual_score >= 0.0
result.footer_protected_change_ratio >= 0.0
```

Also test an explicit `deep` value is forwarded.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py`

- [ ] **Step 3: Integrate without changing the core TDM algorithm**

After `transfer_working_cleanup_to_native(...)` returns the native-size core-cleaned image, call `polish_footer_residual` on that native image. Do not move footer polish into `watermaker TDM.py`.

- [ ] **Step 4: Keep the protected mask conservative**

Provide Footer Polish with a native-resolution content-protect mask when available. Always enforce page-number and upper-content guards even if debug masks are missing.

- [ ] **Step 5: Aggregate document footer metrics**

For document result:

- `footer_residual_score = max(page footer residual scores)`;
- `footer_protected_change_ratio = max(page protected change ratios)`;
- `footer_cleanup_level = "deep"` if any page escalated to Deep, else `"standard"` when enabled.

Store per-page scores in metadata for diagnostics/debugging.

- [ ] **Step 6: Forward footer cleanup from `RasterTemplateStrategy`**

When marker is `tailieuonthi`, pass the runtime footer setting to `clean_tailieuonthi_document`. Add the new metrics to `StrategyResult.metadata`.

- [ ] **Step 7: Run focused tests**

Run: `python -m pytest -q tests/unit/test_footer_polish.py tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py`

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
- Modify: `backend/engine/pipeline_v2/report.py`
- Test: `tests/unit/test_qc_v2.py`

**Interfaces:**
- Extend `QCReport` with defaulted optional fields:

```python
footer_residual_score: float | None = None
footer_cleanup_level: str | None = None
footer_protected_change_ratio: float | None = None
```

- [ ] **Step 1: Write RED QC tests**

Add one report metadata case that passes existing watermark residual checks but fails because `footer_residual_score` is above threshold. Add another that fails because `footer_protected_change_ratio` is above threshold. Add a passing case with all metrics below thresholds.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/unit/test_qc_v2.py`

- [ ] **Step 3: Extend `validate_output` parameters**

Add keyword defaults:

```python
max_footer_residual_score: float = 0.035
max_footer_protected_change_ratio: float = 0.002
```

When metadata contains footer metrics, apply both gates in addition to existing native-outside and watermark-residual gates.

- [ ] **Step 4: Preserve backward compatibility**

If footer metrics are absent, current non-TDM paths behave exactly as before. Do not fail Stream Remove or generic raster output merely because footer metadata is absent.

- [ ] **Step 5: Run QC and integration tests**

Run: `python -m pytest -q tests/unit/test_qc_v2.py tests/integration/test_end_to_end_v2.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/engine/qc_v2/validator.py backend/engine/pipeline_v2/report.py tests/unit/test_qc_v2.py tests/integration/test_end_to_end_v2.py
git commit -m "feat: gate TaiLieuOnThi footer visual quality"
```

---

### Task 7: Add end-to-end diagnostics and synthetic footer regression

**Files:**
- Modify: `tests/integration/test_end_to_end_v2.py`
- Modify: `tests/frontend/test_frontend_contract.py`
- Modify: `frontend/static/app.js`
- Modify: `frontend/index.html`

**Interfaces:**
- Event/report diagnostics: `requested_engine`, `footer_cleanup_level`, `footer_residual_score`, `footer_protected_change_ratio`.

- [ ] **Step 1: Add an E2E synthetic raster PDF fixture**

Generate pages in-test with a full-page image containing a gray footer URL-like watermark, protected colored footer text, page number and footer rule. Feed it through the V2 raster/TDM-guided test seam with the known TaiLieuOnThi marker rather than committing owner PDFs.

- [ ] **Step 2: Assert end-to-end properties**

Assert:

```python
assert output_doc.page_count == input_doc.page_count
assert report.metadata["footer_residual_score"] <= 0.035
assert report.metadata["footer_protected_change_ratio"] <= 0.002
assert report.metadata["footer_cleanup_level"] in {"standard", "deep"}
assert report.ocr_calls == 0
```

Compare synthetic page-number/colored-content guard pixels before/after within test tolerance.

- [ ] **Step 3: Render footer diagnostics in UI**

Add diagnostics fields such as `#analysisFooterCleanup` and `#analysisFooterResidual`. Use compact values (`Deep`, `1.8%` or decimal score consistently) and `—` for non-raster paths.

- [ ] **Step 4: Run frontend and E2E tests**

Run:

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
- No owner PDF binaries committed.

**Interfaces:** none; this is the release gate.

- [ ] **Step 1: Run the full automated suite**

Run:

```bash
python -m compileall -q backend desktop_app.py native_api.py
node --check frontend/static/app.js
python -m pytest -q
```

Expected: all commands PASS.

- [ ] **Step 2: Process both owner regression inputs locally**

Use the cleaned/original TaiLieuOnThi pair supplied by the owner. Run Auto Smart + Balanced + Footer cleanup Auto. Record actual strategy, footer cleanup level, footer residual score, watermark residual score, outside-change ratio, total time and output size.

- [ ] **Step 3: Visually inspect representative footer pages**

Inspect at minimum first, second, a dense middle page, and final page at 100% and 200% zoom. Acceptance:

- no readable/obvious footer URL ghost;
- no rectangular white patch or tone seam;
- page number unchanged;
- colored slogan/teacher line unchanged;
- diagonal watermark cleanup does not regress.

If any criterion fails, do not merge; add the failing crop as a local debug artifact and return to Task 4/5 thresholds/guards.

- [ ] **Step 4: Run the existing Windows/native + Chromium full-stack smoke**

Verify pywebview window construction/NativeApi binding and Chromium primary flow. The engine picker defaults to Auto Smart, footer cleanup defaults to Auto, processing reaches DONE, diagnostics show the actual engine and footer metrics.

- [ ] **Step 5: Update README**

Document Engine preference vs Content protection profile, Footer cleanup Auto/Standard/Deep, and explain that Auto remains recommended.

- [ ] **Step 6: Final verification and commit**

Run the full suite again after docs/UI cleanup, then commit:

```bash
git add README.md
git commit -m "docs: explain engine preferences and footer cleanup"
```

**Merge gate:** automated suite green + Windows/native smoke green + Chromium smoke green + both owner visual regressions accepted.
