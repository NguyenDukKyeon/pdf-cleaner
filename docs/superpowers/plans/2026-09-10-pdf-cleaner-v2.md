# PDF Cleaner V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor PDF Cleaner into an automatic document-level watermark router that preserves vector PDF structure when possible, accelerates raster TaiLieuOnThi-like cleanup, exposes full-stack analysis/progress/QC, and retains legacy engines as verified fallbacks.

**Architecture:** A `DocumentAnalyzer` builds a typed `DocumentProfile`; a `WatermarkRouter` converts it into a typed `ProcessingPlan`; strategy implementations execute structural/vector, raster-template, or legacy processing; V2 QC validates structure/content/watermark residual; the existing pywebview service and frontend expose Auto mode, stage progress and diagnostics. Migration is incremental and keeps the current app runnable after every task.

**Tech Stack:** Python 3, PyMuPDF (`fitz`), OpenCV, NumPy, Pillow, psutil, pywebview, vanilla HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-pdf-cleaner-v2-design.md`

## Global Constraints

- Never implement on `main`; execution branch is `refactor/pdf-cleaner-v2` unless superseded by an explicitly reviewed branch.
- Before **every** task, fetch/read this plan and the approved design from GitHub again.
- Every behavior change follows RED → GREEN → REFACTOR. A passing test written after production code does not satisfy the gate.
- After every implementation checkpoint, update this plan's task state and append current verification evidence to `Execution Log`.
- Keep commits small and independently reviewable; do not mix unrelated cleanup.
- Do not commit the owner's three uploaded regression PDFs. Generate synthetic public-safe fixtures for CI; use owner files only for local/manual regression.
- Preserve pywebview/native desktop architecture; no FastAPI/localhost server.
- Preserve cancellation, staging files, backup-on-overwrite and atomic promotion semantics.
- OCR is optional/lazy and must not become a per-page full-document requirement.
- Legacy TDM/IPCLASS/TYHH/Ebook engines remain until V2 regression and benchmark evidence supports deletion.
- Do not claim speed/quality improvement without benchmark/QC evidence.
- If an assumption about a PDF representation is disproved, stop and amend the plan/design before proceeding.
- Task 0 is baseline restoration/test scaffolding only and does not change production behavior; its gate is source-hash parity plus passing baseline verification. TDD RED begins with Task 1.

---

## Progress

| Task | Deliverable | Status |
|---|---|---|
| 0 | Restore source baseline on feature branch + establish tests/CI harness | IN PROGRESS |
| 1 | Typed analyzer models + representative-page sampling | IN PROGRESS |
| 2 | Structural/raster document analysis + signature registry | NOT STARTED |
| 3 | Deterministic watermark router + processing plans | NOT STARTED |
| 4 | Structural stream/object fast path | NOT STARTED |
| 5 | Native raster image extraction + synthetic raster fixtures | NOT STARTED |
| 6 | Document-level raster consensus/template model | NOT STARTED |
| 7 | V2 executor + safe legacy fallback + multi-worker selection | NOT STARTED |
| 8 | V2 QC + atomic processing service integration | NOT STARTED |
| 9 | pywebview API + Auto-first frontend UX/progress/report | NOT STARTED |
| 10 | Regression benchmark, hardening and legacy-removal decision | NOT STARTED |
| 11 | Final verification, docs and PR readiness | NOT STARTED |

---

### Task 0: Restore source baseline and establish the verification harness

**Files:**
- Restore: current `PDF_Cleaner.zip` source tree into repository root
- Create: `.gitignore`
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/test_baseline_imports.py`
- Create: `.github/workflows/test.yml`
- Modify: `backend/requirements.txt` (add `pytest` only if test dependencies are intentionally installed from this file)
- Modify: this plan after every checkpoint

**Interfaces:**
- Consumes: approved source snapshot from owner upload
- Produces: importable repository, deterministic pytest entrypoint, CI test command `python -m pytest -q`

- [ ] **Step 0.1: Restore all source files to the feature branch without modifying behavior.**

  Verification: compare restored file paths and SHA-256 hashes against the extracted owner ZIP snapshot.

- [ ] **Step 0.2: Read plan/spec again before test harness changes.**

- [ ] **Step 0.3: Add a baseline verification test for restored entrypoints and repository layout.**

  This is a non-behavioral baseline/scaffolding task. Do not fabricate a RED failure after source restoration; source SHA-256 parity is the restore gate and the baseline test must pass.

```python
from pathlib import Path


def test_expected_desktop_entrypoints_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "desktop_app.py").is_file()
    assert (root / "native_api.py").is_file()
    assert (root / "backend" / "service.py").is_file()
    assert (root / "backend" / "engine" / "pdf_pipeline.py").is_file()
    assert (root / "frontend" / "static" / "app.js").is_file()
```

- [ ] **Step 0.4: Add the minimum pytest/CI scaffolding without changing production behavior.**

  CI workflow runs on pushes/PRs and installs the project dependencies plus pytest, then executes `python -m pytest -q`.

- [ ] **Step 0.5: Run the focused baseline verification and confirm PASS.**

  Run: `python -m pytest tests/test_baseline_imports.py -q`

- [ ] **Step 0.6: Run full baseline suite.**

  Run: `python -m pytest -q`

- [ ] **Step 0.7: Update this plan with hashes/test evidence and commit Task 0.**

---

### Task 1: Typed analyzer models and representative-page sampling

**Files:**
- Create: `backend/engine/analyzer/__init__.py`
- Create: `backend/engine/analyzer/models.py`
- Create: `backend/engine/analyzer/document_analyzer.py`
- Test: `tests/unit/test_analyzer_models.py`
- Test: `tests/unit/test_document_sampler.py`
- Modify: this plan

**Interfaces:**
- Produces: `DocumentKind`, `PageEvidence`, `WatermarkCandidate`, `DocumentProfile`, `select_sample_pages(page_count: int, max_samples: int = 8) -> tuple[int, ...]`

- [x] **Step 1.1: Re-read spec/plan and mark Task 1 IN PROGRESS.**
- [x] **Step 1.2: RED — tests define stable sampling and typed profile invariants.**

```python
def test_select_sample_pages_spreads_samples_across_document():
    assert select_sample_pages(20, max_samples=5) == (0, 5, 10, 14, 19)


def test_document_profile_confidence_is_bounded():
    with pytest.raises(ValueError):
        DocumentProfile(page_count=1, kind=DocumentKind.RASTER, confidence=1.1)
```

- [x] **Step 1.3: Run focused tests and verify expected failures.**
- [ ] **Step 1.4: GREEN — implement only typed models + deterministic sampler.**
- [ ] **Step 1.5: Run focused and full tests.**
- [ ] **Step 1.6: Update plan evidence and commit.**

---

### Task 2: Structural/raster analysis and watermark signature registry

**Files:**
- Create: `backend/engine/analyzer/stream_analyzer.py`
- Create: `backend/engine/analyzer/raster_analyzer.py`
- Extend: `backend/engine/analyzer/document_analyzer.py`
- Create: `backend/engine/signatures/__init__.py`
- Create: `backend/engine/signatures/registry.py`
- Create: `backend/engine/signatures/tailieuonthi.json`
- Test: `tests/unit/test_stream_analyzer.py`
- Test: `tests/unit/test_raster_analyzer.py`
- Test: `tests/unit/test_signature_registry.py`
- Create/Test helper: `tests/fixtures_factory.py`
- Modify: this plan

**Interfaces:**
- Consumes: path to PDF and sample page indices
- Produces: populated `DocumentProfile` with text-layer ratio, full-page-image ratio, repeated stream/image evidence and watermark candidates

- [ ] **Step 2.1: Re-read spec/plan; mark IN PROGRESS.**
- [ ] **Step 2.2: RED — generate synthetic PDFs for (a) repeated overlay stream, (b) one full-page image per page, (c) mixed PDF.**
- [ ] **Step 2.3: RED — assert analyzer classifies synthetic documents and registry matches aliases without treating alias match as sufficient proof.**
- [ ] **Step 2.4: Verify tests fail for missing analyzer behavior.**
- [ ] **Step 2.5: GREEN — implement sampled PyMuPDF analysis using existing `core_stream.py` primitives where safe.**
- [ ] **Step 2.6: Run focused + full suite, update plan, commit.**

---

### Task 3: Deterministic watermark router and typed processing plans

**Files:**
- Create: `backend/engine/router/__init__.py`
- Create: `backend/engine/router/models.py`
- Create: `backend/engine/router/router.py`
- Test: `tests/unit/test_router.py`
- Modify: this plan

**Interfaces:**
- Produces: `StrategyKind`, `ProcessingOperation`, `ProcessingPlan`, `build_processing_plan(profile, *, content_profile="auto")`

**Required routing rules:**

```text
isolated repeated structural watermark + high confidence -> STREAM_REMOVE
raster/full-page-image + repeated watermark evidence -> RASTER_TEMPLATE
mixed/uncertain medium confidence -> conservative V2 strategy + strict QC
confidence < 0.70 -> LEGACY
```

- [ ] **Step 3.1: Re-read spec/plan.**
- [ ] **Step 3.2: RED — table-driven router tests for confidence boundaries and evidence combinations.**
- [ ] **Step 3.3: Verify RED.**
- [ ] **Step 3.4: GREEN — pure deterministic router with no PDF I/O.**
- [ ] **Step 3.5: Full suite; plan update; commit.**

---

### Task 4: Structural stream/object fast path

**Files:**
- Create: `backend/engine/strategies/__init__.py`
- Create: `backend/engine/strategies/base.py`
- Create: `backend/engine/strategies/stream_remove.py`
- Refactor compatibility usage: `backend/engine/core_stream.py`
- Test: `tests/integration/test_stream_remove_strategy.py`
- Modify: this plan

**Interfaces:**
- `StreamRemoveStrategy.execute(input_pdf: Path, output_pdf: Path, plan: ProcessingPlan, callbacks...) -> StrategyResult`

- [ ] **Step 4.1: Re-read plan/spec.**
- [ ] **Step 4.2: RED — synthetic PDF contains visible base text plus a repeated isolated `TAILIEUONTHI.NET` overlay stream; after strategy, base text/page count/geometry survive and target stream evidence disappears.**
- [ ] **Step 4.3: Verify RED.**
- [ ] **Step 4.4: GREEN — adapt proven structural logic from `core_stream.py`; do not rasterize pages.**
- [ ] **Step 4.5: Regression: verify unrelated/reused content streams are not removed.**
- [ ] **Step 4.6: Full suite; plan update; commit.**

---

### Task 5: Native raster image extraction fast path

**Files:**
- Create: `backend/engine/raster/__init__.py`
- Create: `backend/engine/raster/image_extractor.py`
- Test: `tests/unit/test_image_extractor.py`
- Test: `tests/integration/test_native_raster_path.py`
- Modify: this plan

**Interfaces:**
- `find_full_page_image(page) -> FullPageImage | None`
- `extract_native_page_image(doc, page_index) -> NativePageImage | None`

- [ ] **Step 5.1: Re-read plan/spec.**
- [ ] **Step 5.2: RED — synthetic raster PDF returns original XObject dimensions/bytes without calling page render.**
- [ ] **Step 5.3: RED — multi-image/vector page returns `None` and explicitly requires render fallback.**
- [ ] **Step 5.4: Verify RED.**
- [ ] **Step 5.5: GREEN — implement conservative geometry/coverage checks and native extraction.**
- [ ] **Step 5.6: Full suite; plan update; commit.**

---

### Task 6: Document-level raster consensus/template model

**Files:**
- Create: `backend/engine/raster/sampler.py`
- Create: `backend/engine/raster/consensus.py`
- Create: `backend/engine/raster/text_guard.py`
- Create: `backend/engine/raster/repair.py`
- Create: `backend/engine/strategies/raster_template.py`
- Test: `tests/unit/test_raster_consensus.py`
- Test: `tests/unit/test_raster_repair.py`
- Test: `tests/integration/test_raster_template_strategy.py`
- Modify: this plan

**Interfaces:**
- `learn_watermark_model(samples, candidates, signature_registry) -> RasterWatermarkModel`
- `RasterTemplateStrategy.execute(...) -> StrategyResult`

- [ ] **Step 6.1: Re-read plan/spec.**
- [ ] **Step 6.2: RED — synthetic pages have changing content plus fixed translucent diagonal watermark; model learns repeated region from sampled pages, not one-page color heuristics.**
- [ ] **Step 6.3: RED — dark formula/line content overlapping watermark remains guarded.**
- [ ] **Step 6.4: Verify RED.**
- [ ] **Step 6.5: GREEN — implement normalized sample consensus/template scoring and minimal repair; no mandatory OCR dependency.**
- [ ] **Step 6.6: Add optional OCR adapter only if tests show structural/template evidence is insufficient; keep import lazy.**
- [ ] **Step 6.7: Full suite; plan update; commit.**

---

### Task 7: V2 executor, safe legacy fallback and multi-worker selection

**Files:**
- Create: `backend/engine/strategies/legacy.py`
- Create: `backend/engine/pipeline_v2/__init__.py`
- Create: `backend/engine/pipeline_v2/analyze.py`
- Create: `backend/engine/pipeline_v2/plan.py`
- Create: `backend/engine/pipeline_v2/execute.py`
- Create: `backend/engine/pipeline_v2/report.py`
- Modify: `backend/engine/tdm_cleaner/core/worker_pool.py` only if tests require worker-selection fixes
- Test: `tests/unit/test_worker_selection.py`
- Test: `tests/integration/test_pipeline_v2_fallback.py`
- Modify: this plan

**Interfaces:**
- `analyze_document(path, options) -> DocumentProfile`
- `plan_document(profile, options) -> ProcessingPlan`
- `execute_plan(input_path, output_path, plan, callbacks) -> ProcessingReport`

- [ ] **Step 7.1: Re-read plan/spec.**
- [ ] **Step 7.2: RED — auto worker selection can exceed one when CPU/RAM/page count permit, remains one when constraints require it.**
- [ ] **Step 7.3: RED — a V2 strategy failure must not corrupt output and routes to legacy only when policy allows.**
- [ ] **Step 7.4: Verify RED.**
- [ ] **Step 7.5: GREEN — implement executor/report/fallback; document-level model created once before page workers.**
- [ ] **Step 7.6: Full suite; plan update; commit.**

---

### Task 8: V2 QC and atomic service integration

**Files:**
- Create: `backend/engine/qc_v2/__init__.py`
- Create: `backend/engine/qc_v2/validator.py`
- Create: `backend/app/__init__.py`
- Create: `backend/app/jobs.py`
- Create: `backend/app/output_service.py`
- Create: `backend/app/qc_service.py`
- Create: `backend/app/processing_service.py`
- Modify: `backend/service.py`
- Test: `tests/unit/test_qc_v2.py`
- Test: `tests/integration/test_atomic_service.py`
- Modify: this plan

**Interfaces:**
- `validate_output(input_pdf, output_pdf, report) -> QCReport`
- service job stages: `QUEUED`, `ANALYZING`, `PLANNING`, `PROCESSING`, `VERIFYING`, `DONE`

- [ ] **Step 8.1: Re-read plan/spec.**
- [ ] **Step 8.2: RED — QC rejects page-count/geometry loss and excessive changes outside watermark mask.**
- [ ] **Step 8.3: RED — failed QC leaves final/source untouched and cleans staging.**
- [ ] **Step 8.4: Verify RED.**
- [ ] **Step 8.5: GREEN — integrate pipeline V2 through focused app services while keeping `backend/service.py` compatibility API.**
- [ ] **Step 8.6: Full suite; plan update; commit.**

---

### Task 9: pywebview API and Auto-first frontend

**Files:**
- Modify: `native_api.py`
- Modify: `frontend/index.html`
- Modify: `frontend/static/app.js`
- Modify: `frontend/static/styles.css`
- Test: `tests/unit/test_native_api_contract.py`
- Test: `tests/frontend/test_frontend_contract.py` (static contract tests in Python unless a JS runner is already introduced deliberately)
- Modify: this plan

**Interfaces:**
- Native API retains `start_process`, `poll_job`, `cancel_job` and adds compatible analysis/report data through existing payloads or a dedicated `analyze_files()` call if required by UX.

- [ ] **Step 9.1: Re-read plan/spec.**
- [ ] **Step 9.2: RED — contract test requires Auto default and removes subject-as-engine selection from the primary workflow.**
- [ ] **Step 9.3: RED — event rendering recognizes ANALYZING/PLANNING/PROCESSING/VERIFYING and processing report fields.**
- [ ] **Step 9.4: Verify RED.**
- [ ] **Step 9.5: GREEN — implement Auto-first UI; move subject options to Advanced content-protection profile.**
- [ ] **Step 9.6: Verify native bridge compatibility and full suite; update plan; commit.**

---

### Task 10: Regression benchmark and hardening

**Files:**
- Create: `scripts/benchmark_v2.py`
- Create: `tests/integration/test_end_to_end_v2.py`
- Create: `docs/benchmarks/2026-09-10-v2-baseline.md`
- Modify: this plan

**Benchmark metrics:**

```text
analysis seconds
processing seconds
QC seconds
total seconds
seconds/page
peak RAM
strategy
worker count
native-image fast-path usage
OCR call count
watermark residual score
outside-mask content change
output file size
```

- [ ] **Step 10.1: Re-read plan/spec.**
- [ ] **Step 10.2: RED — end-to-end synthetic tests assert correct route + valid output for vector, raster and hybrid documents.**
- [ ] **Step 10.3: Verify RED, then implement any minimum hardening required.**
- [ ] **Step 10.4: Run local benchmark against the three owner-provided PDFs without committing them.**
- [ ] **Step 10.5: Record measured legacy vs V2 results. Do not infer missing metrics.**
- [ ] **Step 10.6: Decide explicitly: keep all legacy engines, deprecate a subset, or schedule deletion in a separate approved change.**
- [ ] **Step 10.7: Full suite; plan update; commit.**

---

### Task 11: Final verification, documentation and PR readiness

**Files:**
- Modify: `README.md`
- Modify: design/plan only if implementation facts changed
- Modify: this plan

- [ ] **Step 11.1: Re-read final spec/plan and audit acceptance criteria line by line.**
- [ ] **Step 11.2: Run `python -m pytest -q`.**
- [ ] **Step 11.3: Run syntax/import checks for production modules.**
- [ ] **Step 11.4: Verify GitHub CI on branch/PR head.**
- [ ] **Step 11.5: Compare branch against `main`; ensure no owner PDFs, temp files, `_bootstrap` artifacts introduced by the refactor, or unrelated edits are included.**
- [ ] **Step 11.6: Update README with Auto workflow, Advanced content profiles, strategy/report behavior and troubleshooting.**
- [ ] **Step 11.7: Mark plan COMPLETE only with current verification evidence and prepare PR for review.**

---

## Execution Log

Append one row after every implementation checkpoint. Do not rewrite historical evidence.

| Time (UTC) | Task | Commit/Checkpoint | Verification | Result/Notes |
|---|---|---|---|---|
| 2026-09-09 | Pre-plan | `ec8e68b20db56fb96bb30408977ffe4190821d81` | GitHub commit exists | Initial README/bootstrap started on `main` before feature-branch gate was established. No V2 implementation. |
| 2026-09-09 | Pre-plan | `9a89b03a1f732b42f4ca9809f57a2a52efddd8a0` | Branch head inspected | Bootstrap chunks exist on `main`; source tree not yet restored as normal repository files. |
| 2026-09-10 | Design | `d159a5bbc918986e22c0777518ae8c705efc3a28` | Approved design written to feature branch | No production code changed. |
| 2026-09-10 | Governance | `9ec5cd02a1d38fc0af5cf248d0ecd947f867f792` | `AGENTS.md` added | Execution gates codified before production refactor. |
| 2026-09-10 | Task 0 restore attempt | `c20e376a714c90c91d4f6a36953eb5c83d874a5b` / Actions `34414250113` | `xz --test` failed: `Unexpected end of input` | Bootstrap transport on GitHub had only 8 of 17 archive chunks. No production source was changed; restore strategy must be corrected before continuing. |

| 2026-09-10 | Task 1 RED | `2134294e` + `08b5925e` | Local focused pytest collection fails with `ModuleNotFoundError: backend.engine.analyzer` | Expected RED: analyzer package does not exist yet; production code not written before tests. |

## Acceptance-Criteria Traceability

| Acceptance criterion | Planned task(s) |
|---|---|
| Auto default workflow | 3, 9 |
| Subject profile no longer hard-routes engine | 3, 8, 9 |
| Structural watermark avoids rasterization | 2, 3, 4 |
| Native full-page image extraction | 2, 5 |
| Document-level raster learning | 1, 2, 6 |
| OCR optional/lazy | 2, 6 |
| Safe multi-worker execution | 7 |
| Cancellation/staging/backup/atomic promotion | 7, 8 |
| Structural/content/residual QC | 8 |
| Synthetic vector/raster/hybrid CI coverage | 2, 4, 5, 6, 10 |
| Three owner PDFs locally validated | 10 |
| Legacy kept until evidence supports deletion | 7, 10 |
