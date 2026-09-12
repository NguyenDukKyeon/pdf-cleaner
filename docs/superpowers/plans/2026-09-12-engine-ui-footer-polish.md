# Engine UI and Footer Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose safe engine preferences with clear purpose copy, rename subject-oriented protection labels, add Auto/Standard/Deep footer cleanup, and eliminate visible TaiLieuOnThi footer residue without damaging legitimate footer content.

**Architecture:** Auto V2 remains the safety authority. Manual engine choices are compatibility-gated preferences. Footer cleanup is a separate native-resolution stage after the existing TDM-guided core repair, with its own metrics and QC gates.

**Tech Stack:** Python 3.12, PyMuPDF, NumPy, OpenCV, Pillow, pywebview, vanilla HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-engine-ui-footer-polish-design.md`

## Global Constraints

- Auto Smart remains the default.
- Manual engine preferences always run analyzer compatibility checks.
- Keep content-profile wire values exactly `auto`, `math`, `physics`, `chemistry`, `ebook`.
- Do not expose Vector Repair as a selectable engine.
- Footer cleanup operates only in the trusted footer URL region at native resolution.
- Never change page numbers, legitimate footer branding, or the thin footer rule.
- No mandatory OCR.
- Owner PDFs remain external regression inputs and are not committed without explicit approval.
- Existing page-count, geometry, outside-change, watermark-residual, atomic-output and fallback guarantees remain mandatory.

---

### Task 1: Add compatibility-gated engine preferences

**Files:**
- Modify: `backend/engine/router/models.py`
- Modify: `backend/engine/router/router.py`
- Modify: `tests/unit/test_router.py`

**Interfaces:**

```python
class EnginePreference(str, Enum):
    AUTO_SMART = "auto_smart"
    STREAM_CLEAN = "stream_clean"
    RASTER_CLEAN = "raster_clean"
    COMPATIBILITY_CLEAN = "compatibility_clean"
```

`ProcessingPlan` gains:

```python
requested_engine: str = EnginePreference.AUTO_SMART.value
```

`build_processing_plan(...)` becomes:

```python
def build_processing_plan(
    profile: DocumentProfile,
    *,
    content_profile: str = "auto",
    engine_preference: str = "auto_smart",
) -> ProcessingPlan: ...
```

- [ ] **Step 1: Write RED tests**

Add tests proving:

```python
plan = build_processing_plan(stream_profile, engine_preference="auto_smart")
assert plan.strategy is StrategyKind.STREAM_REMOVE
assert plan.requested_engine == "auto_smart"

with pytest.raises(ValueError, match="Stream Clean"):
    build_processing_plan(raster_profile, engine_preference="stream_clean")

with pytest.raises(ValueError, match="Raster Clean"):
    build_processing_plan(stream_profile, engine_preference="raster_clean")

plan = build_processing_plan(stream_profile, engine_preference="compatibility_clean")
assert plan.strategy is StrategyKind.LEGACY
assert plan.requested_engine == "compatibility_clean"
```

- [ ] **Step 2: Run RED**

`python -m pytest -q tests/unit/test_router.py`

- [ ] **Step 3: Implement one normal Auto plan, then apply preference**

Use `dataclasses.replace` after existing router logic. `stream_clean` accepts only `STREAM_REMOVE`; `raster_clean` accepts only `RASTER_TEMPLATE`; `compatibility_clean` replaces strategy with `LEGACY` and clears operations. Invalid preference values raise `ValueError`.

- [ ] **Step 4: Verify**

`python -m pytest -q tests/unit/test_router.py tests/unit/test_analyzer_models.py tests/unit/test_raster_analyzer.py tests/unit/test_stream_analyzer.py`

- [ ] **Step 5: Commit**

```bash
git add backend/engine/router/models.py backend/engine/router/router.py tests/unit/test_router.py
git commit -m "feat: add compatibility-gated engine preferences"
```

---

### Task 2: Thread engine/footer preferences through native/service execution

**Files:**
- Modify: `native_api.py`
- Modify: `backend/service.py`
- Modify: `backend/engine/pipeline_v2/execute.py`
- Modify: `tests/unit/test_native_api_contract.py`
- Modify: `tests/integration/test_end_to_end_v2.py`

**Interfaces:**

Payload fields:

```text
engine_preference = auto_smart | stream_clean | raster_clean | compatibility_clean
footer_cleanup    = auto | standard | deep
```

- [ ] **Step 1: Write RED NativeApi tests**

With a fake service, assert missing fields are defaulted to:

```python
{"engine_preference": "auto_smart", "footer_cleanup": "auto"}
```

and explicit `raster_clean` / `deep` values are preserved.

- [ ] **Step 2: Run RED**

`python -m pytest -q tests/unit/test_native_api_contract.py`

- [ ] **Step 3: Add NativeApi defaults**

```python
normalized.setdefault("engine_preference", "auto_smart")
normalized.setdefault("footer_cleanup", "auto")
```

- [ ] **Step 4: Validate and forward in `backend/service.py`**

Use fixed allow-sets. Reject invalid values before job creation. Pass `engine_preference` to `build_processing_plan(...)` and `footer_cleanup` to `execute_plan(...)`.

- [ ] **Step 5: Record requested engine in report metadata**

`ProcessingReport` already has `metadata`; keep the dataclass constructor backward compatible and store `requested_engine` plus footer metadata there.

- [ ] **Step 6: Add E2E assertions**

Default execution records `requested_engine == "auto_smart"`; explicit Compatibility Clean produces Legacy and records `compatibility_clean`.

- [ ] **Step 7: Verify and commit**

```bash
python -m pytest -q tests/unit/test_native_api_contract.py tests/integration/test_end_to_end_v2.py
git add native_api.py backend/service.py backend/engine/pipeline_v2/execute.py tests/unit/test_native_api_contract.py tests/integration/test_end_to_end_v2.py
git commit -m "feat: thread engine and footer preferences through V2"
```

---

### Task 3: Redesign engine/protection/footer controls

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/static/app.js`
- Modify: `frontend/static/styles.css`
- Modify: `tests/frontend/test_frontend_contract.py`

**DOM contracts:**

```text
#enginePicker / [data-engine]
#contentProfilePicker / [data-content-profile]
#footerCleanup / [data-footer-cleanup]
```

**State:**

```javascript
let selectedEnginePreference = 'auto_smart';
let selectedContentProfile = 'auto';
let selectedFooterCleanup = 'auto';
```

- [ ] **Step 1: Write RED frontend contract assertions**

Require the exact visible names/copy from the spec for:

- Auto Smart
- Stream Clean
- Raster Clean
- Compatibility Clean
- Formula & Diagram Safe
- Diagram & Line Safe
- Symbol & Structure Safe
- Text & Image Safe
- Footer cleanup Auto / Standard / Deep

Assert no selectable `[data-engine]` says `Vector Repair`.

- [ ] **Step 2: Run RED**

`python -m pytest -q tests/frontend/test_frontend_contract.py`

- [ ] **Step 3: Implement engine cards**

Use an accessible radiogroup/card layout. Auto Smart starts selected. Each card has title + one-line purpose.

- [ ] **Step 4: Replace subject `<select>` with protection cards**

Keep wire values `auto`, `math`, `physics`, `chemistry`, `ebook`; change only labels/copy.

- [ ] **Step 5: Add Footer cleanup control**

Three values: `auto`, `standard`, `deep`; Auto starts selected.

- [ ] **Step 6: Update JS allow-lists and `commonPayload()`**

Emit `engine_preference`, `content_profile`, `footer_cleanup`.

- [ ] **Step 7: Update actual strategy labels**

```javascript
const strategyLabels = {
  stream_remove: 'Stream Clean',
  vector_remove: 'Vector Repair',
  raster_template: 'Raster Clean',
  legacy: 'Compatibility Clean',
};
```

- [ ] **Step 8: Verify and commit**

```bash
node --check frontend/static/app.js
python -m pytest -q tests/frontend/test_frontend_contract.py
git add frontend/index.html frontend/static/app.js frontend/static/styles.css tests/frontend/test_frontend_contract.py
git commit -m "feat: clarify engine and protection controls"
```

---

### Task 4: Implement native-resolution Footer Polish

**Files:**
- Create: `backend/engine/raster/footer_polish.py`
- Create: `tests/unit/test_footer_polish.py`

**Interfaces:**

```python
FooterCleanupLevel = Literal["auto", "standard", "deep"]

@dataclass(frozen=True, slots=True)
class FooterPolishConfig:
    level: FooterCleanupLevel = "auto"
    footer_url_box: tuple[float, float, float, float] = (0.32, 0.955, 0.75, 0.997)
    page_number_guard: tuple[float, float, float, float] = (0.80, 0.955, 1.00, 1.00)
    upper_content_guard_y: float = 0.945
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

- [ ] **Step 1: Generate synthetic footer fixture in tests**

Construct an off-white RGB page with centered pale-gray footer glyphs, colored content above `y=0.945`, a thin legitimate footer rule, and page-number glyphs inside the right guard.

- [ ] **Step 2: Write RED scoring/protection tests**

Assert dirty score `> 0.035`, final score `<= 0.035`, page-number pixels unchanged, upper protected pixels unchanged, and an already-clean fixture produces `changed_pixels == 0` with exact image equality.

- [ ] **Step 3: Run RED**

`python -m pytest -q tests/unit/test_footer_polish.py`

- [ ] **Step 4: Implement native masks**

Build `footer_url_box`, `page_number_guard`, and `upper_content_guard` masks at the native array size. Union guards with `content_protect_mask`.

- [ ] **Step 5: Implement residual score**

Inside the footer ROI, estimate local paper background from unprotected pixels. Candidate residue is low-saturation positive local contrast. Reject protected pixels, broad texture, and legitimate long horizontal rule components. Normalize summed residual contrast by `255 * ROI pixel count`.

- [ ] **Step 6: Implement Standard / Deep / Auto**

Standard uses conservative component selection and one-pixel dilation. Deep uses identical guards with stronger local-background reconstruction and at most two-pixel dilation. Auto runs Standard, rescoring afterward, and runs Deep only when the Standard score remains above `0.035`.

- [ ] **Step 7: Verify and commit**

```bash
python -m pytest -q tests/unit/test_footer_polish.py
git add backend/engine/raster/footer_polish.py tests/unit/test_footer_polish.py
git commit -m "feat: add native footer residual polish"
```

---

### Task 5: Integrate Footer Polish into TDM-guided raster repair

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

`RasterTemplateStrategy.execute(...)` gains `footer_cleanup: str = "auto"`.

- [ ] **Step 1: Write RED forwarding/aggregation tests**

Monkeypatch Footer Polish at the page-clean seam. Assert `footer_cleanup` is forwarded, final residual is document max, protected-change ratio is document max, and document cleanup level becomes `deep` if any page escalates.

- [ ] **Step 2: Run RED**

`python -m pytest -q tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py`

- [ ] **Step 3: Call Footer Polish after `transfer_working_cleanup_to_native(...)`**

Do not modify `watermaker TDM.py`. Footer Polish receives the native-size core-cleaned RGB image.

- [ ] **Step 4: Forward content-protect mask when available**

Resize any TDM debug protection mask to native size with nearest-neighbor interpolation. Footer Polish still enforces page-number/upper guards when no debug mask exists.

- [ ] **Step 5: Aggregate metrics**

```python
footer_residual_score = max(page_scores, default=0.0)
footer_protected_change_ratio = max(page_protected_ratios, default=0.0)
footer_cleanup_level = "deep" if any(level == "deep" for level in page_levels) else "standard"
```

Copy these plus per-page scores into `StrategyResult.metadata`.

- [ ] **Step 6: Verify and commit**

```bash
python -m pytest -q tests/unit/test_footer_polish.py tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py
git add backend/engine/raster/tdm_guided.py backend/engine/strategies/raster_template.py tests/unit/test_tdm_guided.py tests/integration/test_raster_template_strategy.py
git commit -m "feat: polish TaiLieuOnThi footer residuals"
```

---

### Task 6: Add footer-specific QC gates

**Files:**
- Modify: `backend/engine/qc_v2/validator.py`
- Modify: `tests/unit/test_qc_v2.py`

**Interfaces:**

`QCReport` gains defaulted optional fields:

```python
footer_residual_score: float | None = None
footer_cleanup_level: str | None = None
footer_protected_change_ratio: float | None = None
```

`validate_output(...)` gains:

```python
max_footer_residual_score: float = 0.035
max_footer_protected_change_ratio: float = 0.002
```

- [ ] **Step 1: Write RED QC tests**

Existing native/watermark checks pass, but:

```text
footer_residual_score = 0.050 -> FAIL
footer_protected_change_ratio = 0.010 -> FAIL
footer_residual_score = 0.020 and protected = 0.0 -> PASS
```

- [ ] **Step 2: Run RED**

`python -m pytest -q tests/unit/test_qc_v2.py`

- [ ] **Step 3: Implement optional footer gates**

Apply each gate only when its metadata field is present. Non-TDM paths keep current behavior.

- [ ] **Step 4: Verify and commit**

```bash
python -m pytest -q tests/unit/test_qc_v2.py tests/integration/test_end_to_end_v2.py
git add backend/engine/qc_v2/validator.py tests/unit/test_qc_v2.py
git commit -m "feat: gate TaiLieuOnThi footer visual quality"
```

---

### Task 7: Add E2E footer diagnostics/regression

**Files:**
- Modify: `tests/integration/test_end_to_end_v2.py`
- Modify: `tests/frontend/test_frontend_contract.py`
- Modify: `frontend/index.html`
- Modify: `frontend/static/app.js`

**Interfaces:**

```text
#analysisFooterCleanup
#analysisFooterResidual
```

- [ ] **Step 1: Add a generated full-page raster footer fixture**

Create the PDF inside the test with gray footer watermark glyphs, protected colored footer content, page number and footer rule; route through the known TaiLieuOnThi strategy seam.

- [ ] **Step 2: Assert output**

```python
assert output_doc.page_count == input_doc.page_count
assert report.metadata["footer_residual_score"] <= 0.035
assert report.metadata["footer_protected_change_ratio"] <= 0.002
assert report.metadata["footer_cleanup_level"] in {"standard", "deep"}
assert report.ocr_calls == 0
```

Protect page-number and colored-content pixels with exact equality or a one-gray-level tolerance encoded explicitly in the test.

- [ ] **Step 3: Render footer diagnostics**

Show cleanup level as `Standard`/`Deep`, residual as one-decimal percentage, and `—` when not applicable.

- [ ] **Step 4: Verify and commit**

```bash
node --check frontend/static/app.js
python -m pytest -q tests/frontend/test_frontend_contract.py tests/integration/test_end_to_end_v2.py
git add tests/integration/test_end_to_end_v2.py tests/frontend/test_frontend_contract.py frontend/index.html frontend/static/app.js
git commit -m "test: cover footer polish end to end"
```

---

### Task 8: Owner regression + full-stack release gate

**Files:**
- Modify: `README.md`
- Do not add owner PDF binaries.

- [ ] **Step 1: Full automated suite**

```bash
python -m compileall -q backend desktop_app.py native_api.py
node --check frontend/static/app.js
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run owner PDFs**

Use Auto Smart + Balanced + Footer cleanup Auto on the supplied Live 1 and Live 2 TaiLieuOnThi inputs. Record actual strategy, footer cleanup level, footer residual, watermark residual, outside-change ratio, total time and output size.

- [ ] **Step 3: Visual acceptance at 100% and 200%**

Inspect page 1, page 2, one dense middle page and final page. All must satisfy:

- no readable/obvious footer URL ghost;
- no rectangular white patch, halo edge, or tone seam;
- page number unchanged;
- colored teacher/slogan line unchanged;
- diagonal cleanup does not regress.

Any failure blocks merge and returns to Task 4/5.

- [ ] **Step 4: Windows/native + Chromium smoke**

Verify pywebview window construction/NativeApi binding, Auto Smart default, Footer cleanup Auto default, primary flow to DONE, actual-engine diagnostics, footer diagnostics, and no JS/resource errors.

- [ ] **Step 5: Update README**

Document Engine preference vs Content protection profile, Footer cleanup Auto/Standard/Deep, compatibility gates, and Auto Smart as recommended default.

- [ ] **Step 6: Final verification and docs commit**

Run the full suite once more, then:

```bash
git add README.md
git commit -m "docs: explain engine preferences and footer cleanup"
```

**Merge gate:** full automated suite green + Windows/native smoke green + Chromium smoke green + owner visual regressions accepted.
