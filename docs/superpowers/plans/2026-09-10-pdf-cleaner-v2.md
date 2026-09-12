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
| 0 | Restore source baseline on feature branch + establish tests/CI harness | COMPLETE |
| 1 | Typed analyzer models + representative-page sampling | COMPLETE |
| 2 | Structural/raster document analysis + signature registry | COMPLETE |
| 3 | Deterministic watermark router + processing plans | COMPLETE |
| 4 | Structural stream/object fast path | COMPLETE |
| 5 | Native raster image extraction + synthetic raster fixtures | COMPLETE |
| 6 | Document-level raster consensus/template model | COMPLETE |
| 7 | V2 executor + safe legacy fallback + multi-worker selection | COMPLETE |
| 8 | V2 QC + atomic processing service integration | COMPLETE |
| 9 | pywebview API + Auto-first frontend UX/progress/report | COMPLETE |
| 10 | Regression benchmark, hardening and legacy-removal decision | COMPLETE |
| 11 | Final verification, docs and PR readiness | COMPLETE |

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

- [x] **Step 0.1: Restore all source files to the feature branch without modifying behavior.**

  Verification: compare restored file paths and SHA-256 hashes against the extracted owner ZIP snapshot.

- [x] **Step 0.2: Read plan/spec again before test harness changes.**

- [x] **Step 0.3: Add a baseline verification test for restored entrypoints and repository layout.**

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

- [x] **Step 0.4: Add the minimum pytest/CI scaffolding without changing production behavior.**

  CI workflow runs on pushes/PRs and installs the project dependencies plus pytest, then executes `python -m pytest -q`.

- [x] **Step 0.5: Run the focused baseline verification and confirm PASS.**

  Run: `python -m pytest tests/test_baseline_imports.py -q`

- [x] **Step 0.6: Run full baseline suite.**

  Run: `python -m pytest -q`

- [x] **Step 0.7: Update this plan with hashes/test evidence and commit Task 0.**

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
- [x] **Step 1.4: GREEN — implement only typed models + deterministic sampler.**
- [x] **Step 1.5: Run focused and full tests.**
- [x] **Step 1.6: Update plan evidence and commit.**

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

- [x] **Step 2.1: Re-read spec/plan; mark IN PROGRESS.**
- [x] **Step 2.2: RED — generate synthetic PDFs for (a) repeated overlay stream, (b) one full-page image per page, (c) mixed PDF.**
- [x] **Step 2.3: RED — assert analyzer classifies synthetic documents and registry matches aliases without treating alias match as sufficient proof.**
- [x] **Step 2.4: Verify tests fail for missing analyzer behavior.**
- [x] **Step 2.5: GREEN — implement sampled PyMuPDF analysis using existing `core_stream.py` primitives where safe.**
- [x] **Step 2.6: Run focused + full suite, update plan, commit.**

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

- [x] **Step 3.1: Re-read spec/plan.**
- [x] **Step 3.2: RED — table-driven router tests for confidence boundaries and evidence combinations.**
- [x] **Step 3.3: Verify RED.**
- [x] **Step 3.4: GREEN — pure deterministic router with no PDF I/O.**
- [x] **Step 3.5: Full suite; plan update; commit.**

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

- [x] **Step 4.1: Re-read plan/spec.**
- [x] **Step 4.2: RED — synthetic PDF contains visible base text plus a repeated isolated `TAILIEUONTHI.NET` overlay stream; after strategy, base text/page count/geometry survive and target stream evidence disappears.**
- [x] **Step 4.3: Verify RED.**
- [x] **Step 4.4: GREEN — adapt proven structural logic from `core_stream.py`; do not rasterize pages.**
- [x] **Step 4.5: Regression: verify unrelated/reused content streams are not removed.**
- [x] **Step 4.6: Full suite; plan update; commit.**

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

- [x] **Step 5.1: Re-read plan/spec.**
- [x] **Step 5.2: RED — synthetic raster PDF returns original XObject dimensions/bytes without calling page render.**
- [x] **Step 5.3: RED — multi-image/vector page returns `None` and explicitly requires render fallback.**
- [x] **Step 5.4: Verify RED.**
- [x] **Step 5.5: GREEN — implement conservative geometry/coverage checks and native extraction.**
- [x] **Step 5.6: Full suite; plan update; commit.**

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

- [x] **Step 6.1: Re-read plan/spec.**
- [x] **Step 6.2: RED — synthetic pages have changing content plus fixed translucent diagonal watermark; model learns repeated region from sampled pages, not one-page color heuristics.**
- [x] **Step 6.3: RED — dark formula/line content overlapping watermark remains guarded.**
- [x] **Step 6.4: Verify RED.**
- [x] **Step 6.5: GREEN — implement normalized sample consensus/template scoring and minimal repair; no mandatory OCR dependency.**
- [x] **Step 6.6: Add optional OCR adapter only if tests show structural/template evidence is insufficient; keep import lazy.** Decision: not required; consensus/native-image tests and owner regression evidence were sufficient, so no mandatory OCR dependency was added.
- [x] **Step 6.7: Full suite; plan update; commit.**

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

- [x] **Step 7.1: Re-read plan/spec.**
- [x] **Step 7.2: RED — auto worker selection can exceed one when CPU/RAM/page count permit, remains one when constraints require it.**
- [x] **Step 7.3: RED — a V2 strategy failure must not corrupt output and routes to legacy only when policy allows.**
- [x] **Step 7.4: Verify RED.**
- [x] **Step 7.5: GREEN — implement executor/report/fallback; document-level model created once before page workers.**
- [x] **Step 7.6: Full suite; plan update; commit.**

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

- [x] **Step 8.1: Re-read plan/spec.**
- [x] **Step 8.2: RED — QC rejects page-count/geometry loss and excessive changes outside watermark mask.**
- [x] **Step 8.3: RED — failed QC leaves final/source untouched and cleans staging.**
- [x] **Step 8.4: Verify RED.**
- [x] **Step 8.5: GREEN — integrate pipeline V2 through focused app services while keeping `backend/service.py` compatibility API.**
- [x] **Step 8.6: Full suite; plan update; commit.**

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

- [x] **Step 9.1: Re-read plan/spec.**
- [x] **Step 9.2: RED — contract test requires Auto default and removes subject-as-engine selection from the primary workflow.**
- [x] **Step 9.3: RED — event rendering recognizes ANALYZING/PLANNING/PROCESSING/VERIFYING and processing report fields.**
- [x] **Step 9.4: Verify RED.**
- [x] **Step 9.5: GREEN — implement Auto-first UI; move subject options to Advanced content-protection profile.**
- [x] **Step 9.6: Verify native bridge compatibility and full suite; update plan; commit.**

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

- [x] **Step 10.1: Re-read plan/spec.**
- [x] **Step 10.2: RED — end-to-end synthetic tests assert correct route + valid output for vector, raster and hybrid documents.**
- [x] **Step 10.3: Verify RED, then implement any minimum hardening required.**
  - For high-confidence `tailieuonthi` raster documents, benchmark and, if superior, reuse the proven TDM V7 cleanup as a signature-specific repair primitive. This remains Auto/signature routing, not subject routing.
  - Run TDM-guided page repair in short-lived isolated workers; transfer only trusted header/footer/diagonal cleanup back to native pixels. Preserve page geometry and reject unsafe extra page structure.
  - Add a visual-residual quality metric/gate so a visually obvious watermark cannot pass only because outside-mask change is low.
- [x] **Step 10.4: Run local benchmark against the four owner-provided PDF inputs without committing them.** Two uploads may be byte-identical; still run both and record that fact rather than silently collapsing the cases.
- [x] **Step 10.5: Record measured legacy vs V2 results. Do not infer missing metrics.**
- [x] **Step 10.6: Decide explicitly: keep all legacy engines, deprecate a subset, or schedule deletion in a separate approved change.**
- [x] **Step 10.7: Full suite; plan update; commit.**

---

### Task 11: Final verification, documentation and PR readiness

**Files:**
- Modify: `README.md`
- Modify: design/plan only if implementation facts changed
- Modify: this plan

- [x] **Step 11.1: Re-read final spec/plan and audit acceptance criteria line by line.**
- [x] **Step 11.2: Run `python -m pytest -q`.**
- [x] **Step 11.3: Run syntax/import checks for production modules.**
- [x] **Step 11.4: Verify GitHub CI on branch/PR head.**
- [x] **Step 11.5: Compare branch against `main`; ensure no owner PDFs, temp files, `_bootstrap` artifacts introduced by the refactor, or unrelated edits are included.**
- [x] **Step 11.6: Update README with Auto workflow, Advanced content profiles, strategy/report behavior and troubleshooting.**
- [x] **Step 11.7: Mark plan COMPLETE only with current verification evidence and prepare PR for review.**

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

| 2026-09-10 | Task 1 GREEN | `ad2595f6` | Local: focused `11 passed`; full suite `12 passed`; `compileall` analyzer PASS | Typed models and deterministic representative-page sampler implemented after verified RED. |

| 2026-09-10 | Task 8 GREEN | `4a538263e395cc5dcb60cfc7dc0d4b4eaacae70d` / Actions `34481987543` | `py_compile` PASS; focused Task 8 `4 passed`; full suite `41 passed` | Auto compatibility façade now dispatches through V2; stage/QC/report data exposed; atomic service tests remain green. |

| 2026-09-10 | Task 9 RED | `d460902bf0586bf68c625ff492012113fe9a5def` / Actions `34483893202` | Task 9 contracts: `3 failed, 42 passed` | Expected RED: primary UI still selected subject engines, no V2 stage diagnostics, and native start payload omitted Auto defaults. |
| 2026-09-10 | Task 9 GREEN | `39523487a7c36218861d2ac03cd2fbd20ca5cfec` / Actions `34484348250` | `py_compile` PASS; `node --check` PASS; focused Task 9 `4 passed`; full suite `45 passed` | Auto-first desktop workflow, Advanced content-protection profile, V2 stage timeline and report diagnostics implemented while preserving native job bridge. |
| 2026-09-12 | Task 10 GREEN + benchmark | `b6dd6dd43c34509e5589a97e708329bb68b4cfad` / Actions `34669911933` | Payload SHA-256 verified; `python -m pytest -q`: `59 passed in 11.39s`; `compileall` PASS; four owner-input benchmark runs recorded locally | Added TDM-guided TaiLieuOnThi raster cleanup, residual QC gate `<= 0.08`, native-pixel preservation outside trusted watermark zones, output-size guard, and benchmark report. All four V2 inputs passed QC; keep all legacy engines as compatibility fallbacks. |
| 2026-09-12 | Task 10 final CI gate | `4fd6beb39de12e609e71698640486092f218ca0b` / Actions `34670226489` | Normal branch `Tests` workflow SUCCESS after verified implementation and plan checkpoint | Task 10 completion gate satisfied without relaxing residual, outside-change, output-size, regression-test, or benchmark requirements. |

| 2026-09-10 | Task 0 audited closure | `eb2510b3e02a4443bc37d3f9209fd82beb7492e3`; baseline test `027f08e629ebc1a9f1028851f1d8699176898dc9` | Restore transport SHA-256 `82a7b105afeeab387f3d805a09ef769de195a4c876396f42a1265d1e28d8997e` verified; restored `backend/service.py`, `config_manager.py`, and `watermaker TYHH.py` hashes verified; final CI includes baseline layout test | Earlier truncated bootstrap attempt was superseded by hash-verified owner-source restoration. Test/CI scaffolding now matches the plan. |
| 2026-09-10 | Task 6 RED → GREEN audit | RED `419a107081a239a768fb16ebf6e230e0dbb7bc2d`; GREEN `83006b46aaebf6f984d73c84dba1972c7ae38da3`; bounded-sample follow-up `de70f29002f4e43db334f6547cf0b0035806a630` | RED tests preceded raster consensus implementation; current suite covers cross-page consensus, dark-content guard, native extraction, strategy integration and output-size guard | Task 6 was implemented but its checklist status drifted. Optional OCR was deliberately unnecessary; OCR remains lazy/optional by design. |
| 2026-09-12 | Task 11 final verification | HEAD `73f70a83d963b6a31243eeaeed0f6b06fc58f731` / Actions `34671309846` | Python `compileall` PASS; `node --check frontend/static/app.js` PASS; `python -m pytest -q`: `60 passed in 15.05s`; branch-vs-main diff audited | README documents Auto V2/Advanced/QC/troubleshooting; temporary recovery/export workflows and `_bootstrap_supp` removed; no owner PDFs appear in final diff. Branch is PR-ready; legacy fallbacks intentionally retained per Task 10 evidence. |

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
