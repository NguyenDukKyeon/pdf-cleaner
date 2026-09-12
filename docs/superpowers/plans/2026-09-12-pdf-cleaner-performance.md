# PDF Cleaner Safe Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Auto V2 analysis and raster-processing time while preserving all existing QC gates plus the new footer-visual gates defined by the V2.1 design.

**Architecture:** Optimize only measured bottlenecks. Establish a reproducible benchmark first, then implement staged analysis, a conservative in-process analysis cache, and bounded concurrency for the already-isolated TaiLieuOnThi page workers. Every optimization is accepted only when the same regression inputs pass the same quality thresholds before and after.

**Tech Stack:** Python 3.12, PyMuPDF, NumPy, OpenCV, subprocess workers, `concurrent.futures`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-engine-ui-footer-polish-design.md`

## Global Constraints

- Do not reduce QC thresholds to claim a speed improvement.
- Footer residual QC applies to Fast, Balanced, High Quality and Safe presets.
- OCR remains optional and must not become a per-page requirement.
- Do not share or mutate one `fitz.Document` concurrently across worker threads/processes.
- The owner PDFs may be used for local benchmarks but must not be committed without explicit approval.
- Record before/after timing using the same machine, preset, input and output-quality gates.
- Safe preset must retain single-worker semantics where currently required.
- If an optimization is slower or materially increases memory without a compensating benefit, revert it rather than keeping complexity.

---

## File structure

**Create**

- `backend/engine/pipeline_v2/analysis_cache.py` — small process-local cache and file fingerprinting.
- `scripts/benchmark_v2.py` — repeatable local benchmark/report tool.
- `tests/unit/test_analysis_cache.py`.

**Modify**

- `backend/engine/analyzer/document_analyzer.py` — staged 3/5/8-page evidence acquisition.
- `backend/engine/pipeline_v2/analyze.py` — cache integration.
- `backend/engine/pipeline_v2/execute.py` — pass bounded worker count to raster strategy.
- `backend/engine/strategies/raster_template.py` — accept runtime worker count.
- `backend/engine/raster/tdm_guided.py` — run isolated page subprocesses concurrently and assemble in page order.
- `backend/service.py` — expose benchmark-friendly timing/diagnostics if needed without changing public behavior.
- `tests/unit/test_document_sampler.py`.
- `tests/unit/test_tdm_guided.py`.
- `tests/unit/test_worker_selection.py`.
- `tests/integration/test_end_to_end_v2.py`.

---

### Task 1: Establish a reproducible baseline benchmark before optimization

**Files:**
- Create: `scripts/benchmark_v2.py`
- Test: lightweight unit/import test if repository convention requires it.

**Interfaces:**
- CLI input: one or more PDF paths, `--preset`, `--repeat`, `--output-dir`.
- JSON output contains per-run and aggregate timing/quality metadata.

- [ ] **Step 1: Write the benchmark CLI skeleton test first**

If the repository has no CLI test convention, write a small test that imports `scripts.benchmark_v2` and calls a pure formatter/aggregate helper:

```python
def test_summarize_runs_reports_median_and_pages_per_second():
    summary = summarize_runs([
        {"total_seconds": 10.0, "pages": 20},
        {"total_seconds": 8.0, "pages": 20},
        {"total_seconds": 12.0, "pages": 20},
    ])
    assert summary["median_total_seconds"] == 10.0
    assert summary["median_pages_per_second"] == 2.0
```

- [ ] **Step 2: Run the test and verify RED**

Run the focused test and confirm the module/helper does not exist.

- [ ] **Step 3: Implement `scripts/benchmark_v2.py`**

The script must:

1. run through the same V2 analyze/plan/execute/QC path as production rather than calling legacy helpers directly;
2. collect `analyze_seconds`, `processing_seconds`, `qc_seconds` when available, `total_seconds`, page count, pages/sec, output size, strategy, confidence, worker count, native-image pages, OCR calls, footer residual score, watermark residual score and outside-change ratio;
3. support warmup plus `--repeat N`;
4. write one JSON report next to benchmark outputs;
5. never overwrite source PDFs.

- [ ] **Step 4: Capture baseline evidence**

Run Balanced on the owner regression PDFs plus existing synthetic E2E fixtures. Save the JSON report outside version control or under an ignored benchmark-output directory.

- [ ] **Step 5: Commit**

```bash
git add scripts/benchmark_v2.py tests
git commit -m "test: add reproducible V2 benchmark harness"
```

---

### Task 2: Implement staged 3 → 5 → 8 page analysis

**Files:**
- Modify: `backend/engine/analyzer/document_analyzer.py`
- Modify: `backend/engine/pipeline_v2/analyze.py`
- Test: `tests/unit/test_document_sampler.py`
- Test: `tests/unit/test_stream_analyzer.py`
- Test: `tests/unit/test_raster_analyzer.py`

**Interfaces:**
- Keep public `analyze_document(path, *, max_samples=8, signature_registry=None) -> DocumentProfile` backward compatible.
- Add internal helper `select_staged_sample_pages(page_count: int, stages: tuple[int, ...] = (3, 5, 8)) -> tuple[tuple[int, ...], ...]`.
- Add internal helper `_profile_from_evidence(...)` so earlier evidence is reused when expanding a stage.

- [ ] **Step 1: Write failing staged-sampling tests**

Required cases:

```python
assert select_staged_sample_pages(2) == ((0, 1),)
assert len(select_staged_sample_pages(20)[0]) == 3
assert len(select_staged_sample_pages(20)[1]) == 5
assert len(select_staged_sample_pages(20)[2]) == 8
assert set(stage3).issubset(stage5)
assert set(stage5).issubset(stage8)
```

Add an analyzer test proving a strong repeated-stream fixture reads only 3 sampled pages, while an ambiguous fixture expands to 8. Use monkeypatch/spies around the per-page evidence collector rather than timing assertions.

- [ ] **Step 2: Run analyzer tests and verify RED**

Run:

```bash
python -m pytest -q tests/unit/test_document_sampler.py tests/unit/test_stream_analyzer.py tests/unit/test_raster_analyzer.py
```

- [ ] **Step 3: Refactor evidence collection without changing classification formulas**

Extract one-page evidence collection into an internal function. Preserve existing `DocumentKind` thresholds, signature matching, repeated-stream evidence and TaiLieuOnThi probe semantics.

- [ ] **Step 4: Add early-stop confidence rules**

Use staged samples in deterministic order. Stop early only when both representation and watermark evidence are strong:

```python
best = max(profile.watermark_candidates, key=lambda c: c.confidence, default=None)
strong = profile.confidence >= 0.95 and best is not None and best.confidence >= 0.95
```

At stage 3: stop only on `strong`.

At stage 5: stop when representation confidence is at least `0.90` and best candidate confidence is at least `0.90`.

If there is no candidate, confidence is below the thresholds, or the document remains hybrid/ambiguous, continue to the next stage up to `max_samples`.

Do not lower the existing router confidence bands.

- [ ] **Step 5: Ensure final `sampled_pages` reports only pages actually inspected**

The returned `DocumentProfile.sampled_pages` must be the exact pages read, not the theoretical 8-page set.

- [ ] **Step 6: Run analyzer and router suites**

Run:

```bash
python -m pytest -q tests/unit/test_document_sampler.py tests/unit/test_stream_analyzer.py tests/unit/test_raster_analyzer.py tests/unit/test_router.py
```

Expected: PASS.

- [ ] **Step 7: Benchmark and keep only if it helps**

Compare analyze time on long high-confidence structural PDFs. Quality/strategy selection must remain unchanged. If the staged implementation does not reduce median analyze time on the target workload, do not merge the complexity.

- [ ] **Step 8: Commit**

```bash
git add backend/engine/analyzer/document_analyzer.py backend/engine/pipeline_v2/analyze.py tests/unit/test_document_sampler.py tests/unit/test_stream_analyzer.py tests/unit/test_raster_analyzer.py
git commit -m "perf: add confidence-gated staged document analysis"
```

---

### Task 3: Add a conservative process-local analysis cache

**Files:**
- Create: `backend/engine/pipeline_v2/analysis_cache.py`
- Modify: `backend/engine/pipeline_v2/analyze.py`
- Create: `tests/unit/test_analysis_cache.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class AnalysisFingerprint:
    size: int
    mtime_ns: int
    sample_sha256: str


def fingerprint_pdf(path: str | Path, sample_bytes: int = 65536) -> AnalysisFingerprint: ...

class AnalysisCache:
    def get(self, path: Path, fingerprint: AnalysisFingerprint) -> DocumentProfile | None: ...
    def put(self, path: Path, fingerprint: AnalysisFingerprint, profile: DocumentProfile) -> None: ...
    def clear(self) -> None: ...
```

- [ ] **Step 1: Write RED cache tests**

Tests must prove:

- same unchanged file returns the cached profile;
- touching/changing mtime invalidates it;
- changing first/last sampled bytes invalidates it even when size is unchanged;
- cache is bounded (for example 32 entries) and evicts oldest/LRU entries;
- no PDF bytes beyond the small fingerprint sample are retained.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest -q tests/unit/test_analysis_cache.py`

- [ ] **Step 3: Implement fingerprinting**

Hash: file size + first 64 KiB + last 64 KiB (or whole file when smaller) using SHA-256. Include `st_mtime_ns` in the fingerprint.

- [ ] **Step 4: Implement a bounded in-memory cache**

Use `collections.OrderedDict` or another simple LRU. Default max entries: 32. Store typed `DocumentProfile`, not mutable `fitz` objects.

- [ ] **Step 5: Integrate in `pipeline_v2/analyze.py`**

The production analyze wrapper computes the fingerprint, checks cache, calls `analyze_document` on miss, then stores the result. Expose a cache-clear function for tests/application reset if needed.

- [ ] **Step 6: Run cache + analyzer + E2E tests**

Run:

```bash
python -m pytest -q tests/unit/test_analysis_cache.py tests/unit/test_document_sampler.py tests/integration/test_end_to_end_v2.py
```

- [ ] **Step 7: Benchmark repeated processing**

Run the same unchanged PDF twice. Second analysis should be materially faster and must report the exact same `DocumentProfile`/strategy evidence.

- [ ] **Step 8: Commit**

```bash
git add backend/engine/pipeline_v2/analysis_cache.py backend/engine/pipeline_v2/analyze.py tests/unit/test_analysis_cache.py
git commit -m "perf: cache unchanged document analysis"
```

---

### Task 4: Run isolated TaiLieuOnThi page workers concurrently

**Files:**
- Modify: `backend/engine/raster/tdm_guided.py`
- Modify: `backend/engine/strategies/raster_template.py`
- Modify: `backend/engine/pipeline_v2/execute.py`
- Test: `tests/unit/test_tdm_guided.py`
- Test: `tests/unit/test_worker_selection.py`
- Test: `tests/integration/test_raster_template_strategy.py`

**Interfaces:**
- `clean_tailieuonthi_document(..., workers: int = 1, footer_cleanup: str = "auto") -> TdmGuidedResult`.
- `RasterTemplateStrategy.execute(..., workers: int = 1, footer_cleanup: str = "auto", ...)`.
- `execute_plan` passes its already-computed bounded `worker_count` to RasterTemplateStrategy as well as LegacyStrategy.

- [ ] **Step 1: Write RED concurrency tests with a fake page worker**

Monkeypatch `_run_page_worker` to sleep briefly and record active worker count. For a four-page input and `workers=2`, assert:

```python
assert max_active_workers == 2
assert assembled_page_order == [0, 1, 2, 3]
```

For `workers=1`, assert serial behavior. Add a cancellation test ensuring pending work is not submitted indefinitely after cancellation.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/unit/test_tdm_guided.py tests/unit/test_worker_selection.py`

- [ ] **Step 3: Add bounded concurrency in the parent only**

Use `concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers))` to launch the already-isolated `_run_page_worker` subprocess calls. The thread pool coordinates child processes; it must never share a mutable `fitz.Document`.

- [ ] **Step 4: Preserve deterministic output order**

Collect `(page_index, page_png, metrics)` results by index. Update progress as pages complete, but call `_assemble_native_pdf` only after all successful results are present and sort page paths by page index.

- [ ] **Step 5: Handle failure/cancellation atomically**

On the first failed page:

- stop scheduling new work where practical;
- cancel pending futures;
- allow running child processes to terminate through the existing cancellation path;
- do not assemble/promote partial output;
- let temporary-directory cleanup remove page PNG/JSON artifacts.

- [ ] **Step 6: Pass worker count through RasterTemplateStrategy**

Modify `execute_plan` so both LegacyStrategy and RasterTemplateStrategy receive the computed `workers`. Safe mode must still result in one worker.

- [ ] **Step 7: Run focused and E2E tests**

Run:

```bash
python -m pytest -q tests/unit/test_tdm_guided.py tests/unit/test_worker_selection.py tests/integration/test_raster_template_strategy.py tests/integration/test_end_to_end_v2.py
```

Expected: PASS.

- [ ] **Step 8: Benchmark TaiLieuOnThi PDFs**

Compare Balanced median processing time for `workers=1` vs auto worker count. Record peak practical worker count and output/QC metrics. Keep a hard cap only if benchmark/memory evidence shows it is necessary.

- [ ] **Step 9: Commit**

```bash
git add backend/engine/raster/tdm_guided.py backend/engine/strategies/raster_template.py backend/engine/pipeline_v2/execute.py tests/unit/test_tdm_guided.py tests/unit/test_worker_selection.py tests/integration/test_raster_template_strategy.py tests/integration/test_end_to_end_v2.py
git commit -m "perf: parallelize isolated TaiLieuOnThi page repair"
```

---

### Task 5: Avoid redundant encoding/replacement work in raster strategies

**Files:**
- Modify: `backend/engine/strategies/raster_template.py`
- Test: `tests/integration/test_raster_template_strategy.py`
- Test: `tests/unit/test_raster_repair.py`

**Interfaces:** no new public API.

- [ ] **Step 1: Add tests proving unchanged pages/xrefs are not encoded/replaced**

Monkeypatch image encoding or `replace_image` and verify:

- `pixels == 0` never invokes PNG encoding/replacement;
- reused XRefs are processed once;
- pages with the same XRef still report progress correctly.

- [ ] **Step 2: Run RED or confirm current coverage gap**

Run: `python -m pytest -q tests/integration/test_raster_template_strategy.py tests/unit/test_raster_repair.py`

If current behavior already passes the exact assertions, keep the tests and make no production change for that subcase.

- [ ] **Step 3: Make only measured minimal changes**

Do not introduce speculative page classifiers. Preserve the current `processed_xrefs` optimization and the `if pixels:` encode/replace guard. If profiling shows redundant decode/resize work before the zero-change decision, remove only that measured duplication.

- [ ] **Step 4: Benchmark generic raster path**

Record processing time and output size before/after. Revert production edits if the benchmark improvement is within noise.

- [ ] **Step 5: Commit tests/optimization**

```bash
git add backend/engine/strategies/raster_template.py tests/integration/test_raster_template_strategy.py tests/unit/test_raster_repair.py
git commit -m "perf: avoid redundant raster replacement work"
```

---

### Task 6: Verify preset behavior and expose performance diagnostics without weakening quality

**Files:**
- Modify only if required: `backend/service.py`
- Modify only if required: `frontend/static/app.js`
- Modify: `README.md`
- Test: `tests/frontend/test_frontend_contract.py`
- Test: relevant preset/config tests.

**Interfaces:** existing Fast/Balanced/High Quality/Safe names remain unchanged.

- [ ] **Step 1: Add/extend tests for preset invariants**

Verify:

- Fast uses its configured lower DPI/quality values but still runs V2 QC/footer QC;
- Balanced remains default;
- High Quality uses its higher configured DPI values;
- Safe forces one worker;
- no preset sets `footer_cleanup=off` or disables footer QC.

- [ ] **Step 2: Run preset/config tests**

Run the focused tests and confirm current behavior or RED failures.

- [ ] **Step 3: Add timing diagnostics only where already available**

If the production report already contains `processing_seconds`/`total_seconds`, surface them in technical diagnostics/logs. Do not add expensive timing instrumentation around every page unless benchmark analysis requires it.

- [ ] **Step 4: Update README performance guidance**

Document:

- Fast for speed-sensitive work;
- Balanced as recommended default;
- High Quality for output fidelity;
- Safe for conservative single-worker execution;
- analysis cache applies only to unchanged files within the current app process;
- Auto Smart still chooses the safe strategy.

- [ ] **Step 5: Commit**

```bash
git add backend/service.py frontend/static/app.js README.md tests
git commit -m "docs: clarify safe performance presets"
```

---

### Task 7: Final before/after benchmark and release gate

**Files:** no mandatory production changes; benchmark report is not committed unless it contains no owner/private paths and the owner wants it retained.

- [ ] **Step 1: Run full automated verification**

```bash
python -m compileall -q backend desktop_app.py native_api.py
node --check frontend/static/app.js
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run before/after benchmark matrix**

For each owner regression PDF and representative synthetic fixture, run at least Balanced three times after one warmup. Compare against the Task 1 baseline.

Record:

```text
analyze_seconds
processing_seconds
qc_seconds
total_seconds
pages_per_second
strategy
worker_count
native_image_pages
ocr_calls
output_size
footer_residual_score
watermark_residual_score
outside_change_ratio
```

- [ ] **Step 3: Evaluate each optimization independently**

Accept a change only when:

- median time improves beyond normal run-to-run noise;
- selected strategy is still correct;
- all structural/content/footer QC gates pass;
- output size does not grow unreasonably without a documented quality benefit;
- memory/CPU behavior remains acceptable on the target desktop.

Remove any optimization that fails these criteria.

- [ ] **Step 4: Run Windows/native and Chromium full-stack smoke**

Confirm no UI/native regression and successful processing through DONE with diagnostics populated.

- [ ] **Step 5: Final owner-PDF visual check**

Inspect first, second, dense middle and final pages. Performance work must not reintroduce footer ghosts, diagonal watermark residue, broken formulas/graphs, or damaged page numbers.

- [ ] **Step 6: Final commit only if needed**

If benchmark-driven cleanup changed code/docs, commit with a narrow message. Otherwise leave the prior task commits as the final implementation history.

**Merge gate:** full test suite green + full-stack smoke green + quality metrics pass + visual owner regression accepted + measured speed improvement documented.
