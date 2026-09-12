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
- Owner PDFs may be used for local benchmarks but must not be committed without explicit approval.
- Record before/after timing using the same machine, preset, input and output-quality gates.
- Safe preset retains single-worker semantics.
- Remove any optimization that is slower or materially increases memory without a measured compensating benefit.

---

## File structure

**Create**

- `backend/engine/pipeline_v2/analysis_cache.py` — bounded process-local analysis cache and file fingerprinting.
- `scripts/benchmark_v2.py` — repeatable benchmark/report CLI.
- `tests/unit/test_analysis_cache.py`.
- `tests/unit/test_benchmark_v2.py`.

**Modify**

- `backend/engine/analyzer/document_analyzer.py` — staged 3/5/8-page evidence acquisition.
- `backend/engine/pipeline_v2/analyze.py` — cache integration.
- `backend/engine/pipeline_v2/execute.py` — pass bounded worker count to raster strategy.
- `backend/engine/strategies/raster_template.py` — accept runtime worker count.
- `backend/engine/raster/tdm_guided.py` — run isolated page subprocesses concurrently and assemble in page order.
- `tests/unit/test_document_sampler.py`.
- `tests/unit/test_tdm_guided.py`.
- `tests/unit/test_worker_selection.py`.
- `tests/integration/test_raster_template_strategy.py`.
- `tests/integration/test_end_to_end_v2.py`.
- `tests/frontend/test_frontend_contract.py`.
- `README.md`.

---

### Task 1: Establish a reproducible baseline benchmark before optimization

**Files:**
- Create: `scripts/benchmark_v2.py`
- Create: `tests/unit/test_benchmark_v2.py`

**Interfaces:**
- CLI: `python scripts/benchmark_v2.py <pdf> [<pdf> ...] --preset balanced --repeat 3 --output-dir <dir>`.
- Function: `summarize_runs(runs: list[dict[str, float | int | str]]) -> dict[str, float]`.
- JSON report contains one record per run plus aggregate medians.

- [ ] **Step 1: Write the failing benchmark-helper test**

Create `tests/unit/test_benchmark_v2.py`:

```python
from scripts.benchmark_v2 import summarize_runs


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

Run: `python -m pytest -q tests/unit/test_benchmark_v2.py`

Expected: import failure because `scripts/benchmark_v2.py` does not exist.

- [ ] **Step 3: Implement `scripts/benchmark_v2.py`**

The script must run the same V2 analyze → plan → execute → QC path as production and collect:

```text
analyze_seconds
processing_seconds
qc_seconds
total_seconds
pages
pages_per_second
strategy
confidence
worker_count
native_image_pages
ocr_calls
output_size_bytes
footer_residual_score
watermark_residual_score
outside_change_ratio
```

Use `time.perf_counter()` around each stage. Never overwrite source PDFs. Write outputs into `--output-dir` and emit one JSON report containing raw runs plus `summarize_runs(...)` results.

- [ ] **Step 4: Run unit test and capture baseline**

Run:

```bash
python -m pytest -q tests/unit/test_benchmark_v2.py
python scripts/benchmark_v2.py <owner-pdf-1> <owner-pdf-2> --preset balanced --repeat 3 --output-dir <local-benchmark-dir>
```

Store the baseline JSON outside version control.

- [ ] **Step 5: Commit**

```bash
git add scripts/benchmark_v2.py tests/unit/test_benchmark_v2.py
git commit -m "test: add reproducible V2 benchmark harness"
```

---

### Task 2: Implement staged 3 → 5 → 8 page analysis

**Files:**
- Modify: `backend/engine/analyzer/document_analyzer.py`
- Modify: `backend/engine/pipeline_v2/analyze.py`
- Modify: `tests/unit/test_document_sampler.py`
- Modify: `tests/unit/test_stream_analyzer.py`
- Modify: `tests/unit/test_raster_analyzer.py`

**Interfaces:**
- Keep `analyze_document(path, *, max_samples=8, signature_registry=None) -> DocumentProfile` backward compatible.
- Add `select_staged_sample_pages(page_count: int, stages: tuple[int, ...] = (3, 5, 8)) -> tuple[tuple[int, ...], ...]`.
- Add internal `_collect_page_evidence(...)` and `_profile_from_evidence(...)` helpers so previously inspected pages are not read twice.

- [ ] **Step 1: Write failing staged-sampling tests**

Add tests:

```python
stages = select_staged_sample_pages(20)
assert len(stages[0]) == 3
assert len(stages[1]) == 5
assert len(stages[2]) == 8
assert set(stages[0]).issubset(stages[1])
assert set(stages[1]).issubset(stages[2])
assert select_staged_sample_pages(2) == ((0, 1),)
```

Add a spy around `_collect_page_evidence` proving a high-confidence repeated-stream fixture inspects 3 pages while an ambiguous fixture expands to 8.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest -q tests/unit/test_document_sampler.py tests/unit/test_stream_analyzer.py tests/unit/test_raster_analyzer.py
```

- [ ] **Step 3: Refactor evidence collection without changing classification formulas**

Extract per-page evidence collection while preserving current `DocumentKind` thresholds, signature matching, repeated-stream evidence, and TaiLieuOnThi probe semantics.

- [ ] **Step 4: Add deterministic early-stop rules**

After each stage, build a temporary profile and compute:

```python
best = max(profile.watermark_candidates, key=lambda c: c.confidence, default=None)
```

Stage 3 stops only when:

```python
profile.confidence >= 0.95 and best is not None and best.confidence >= 0.95
```

Stage 5 stops only when:

```python
profile.confidence >= 0.90 and best is not None and best.confidence >= 0.90
```

No candidate, hybrid ambiguity, or lower confidence always expands to the next stage up to `max_samples`.

- [ ] **Step 5: Return only actually inspected sample pages**

`DocumentProfile.sampled_pages` must exactly match the evidence pages collected before the stop condition.

- [ ] **Step 6: Run analyzer + router suites**

```bash
python -m pytest -q tests/unit/test_document_sampler.py tests/unit/test_stream_analyzer.py tests/unit/test_raster_analyzer.py tests/unit/test_router.py
```

Expected: PASS.

- [ ] **Step 7: Benchmark staged analysis**

Use `scripts/benchmark_v2.py` on long high-confidence PDFs. Keep the change only if median `analyze_seconds` improves and the selected strategy/confidence band remains equivalent.

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
    def __init__(self, max_entries: int = 32): ...
    def get(self, path: Path, fingerprint: AnalysisFingerprint) -> DocumentProfile | None: ...
    def put(self, path: Path, fingerprint: AnalysisFingerprint, profile: DocumentProfile) -> None: ...
    def clear(self) -> None: ...
```

- [ ] **Step 1: Write RED cache tests**

Test that:

- unchanged file + identical fingerprint hits;
- mtime change misses;
- first/last sampled-byte change misses even when size is unchanged;
- 33 inserts into a 32-entry cache evict the least-recently-used entry;
- cached values are `DocumentProfile` objects, never open `fitz.Document` handles or full PDF bytes.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest -q tests/unit/test_analysis_cache.py`

- [ ] **Step 3: Implement fingerprinting**

Hash file size + first 64 KiB + last 64 KiB with SHA-256, and include `st_mtime_ns` in `AnalysisFingerprint`.

- [ ] **Step 4: Implement the 32-entry LRU**

Use `collections.OrderedDict`. Move hits to the end; pop the oldest entry after inserts that exceed `max_entries`.

- [ ] **Step 5: Integrate cache into `pipeline_v2/analyze.py`**

Create one module-level `AnalysisCache(max_entries=32)`. On each analysis call: fingerprint → lookup → analyze on miss → store → return. Add `clear_analysis_cache()` for tests/app reset.

- [ ] **Step 6: Run cache + E2E tests**

```bash
python -m pytest -q tests/unit/test_analysis_cache.py tests/unit/test_document_sampler.py tests/integration/test_end_to_end_v2.py
```

Expected: PASS.

- [ ] **Step 7: Benchmark repeated unchanged input**

Process the same PDF twice in one app process. The second `analyze_seconds` must fall materially while strategy/profile data remain identical.

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
- Modify: `tests/unit/test_tdm_guided.py`
- Modify: `tests/unit/test_worker_selection.py`
- Modify: `tests/integration/test_raster_template_strategy.py`
- Modify: `tests/integration/test_end_to_end_v2.py`

**Interfaces:**
- `clean_tailieuonthi_document(..., workers: int = 1, footer_cleanup: str = "auto") -> TdmGuidedResult`.
- `RasterTemplateStrategy.execute(..., workers: int = 1, footer_cleanup: str = "auto", ...)`.
- `execute_plan` passes its already-computed bounded worker count to `RasterTemplateStrategy` and `LegacyStrategy`.

- [ ] **Step 1: Write RED concurrency tests with a fake page worker**

Monkeypatch `_run_page_worker` to sleep briefly and record current/maximum active calls. For a four-page input and `workers=2` assert:

```python
assert max_active_workers == 2
assert assembled_page_order == [0, 1, 2, 3]
```

For `workers=1`, assert `max_active_workers == 1`. Add a cancellation test that sets `should_cancel()` true and verifies no final PDF is assembled.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/unit/test_tdm_guided.py tests/unit/test_worker_selection.py`

- [ ] **Step 3: Add bounded parent-side concurrency**

Use:

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
    ...
```

Each thread may launch one existing isolated `_run_page_worker` subprocess. No thread receives a shared mutable `fitz.Document`.

- [ ] **Step 4: Preserve deterministic output order**

Store each completed result under its page index. Progress may update in completion order, but `_assemble_native_pdf(...)` receives `page_pngs` sorted by page index only after every page succeeds.

- [ ] **Step 5: Preserve atomic failure/cancellation**

On one page failure or cancellation: cancel pending futures, terminate/allow existing `_run_page_worker` cancellation to terminate running children, skip PDF assembly, and rely on the temporary directory for cleanup.

- [ ] **Step 6: Pass computed worker count through V2 execution**

`execute_plan` must pass `workers` to `RasterTemplateStrategy`; Safe preset remains `workers == 1` through the existing `auto_worker_count` selection path.

- [ ] **Step 7: Run focused + integration tests**

```bash
python -m pytest -q tests/unit/test_tdm_guided.py tests/unit/test_worker_selection.py tests/integration/test_raster_template_strategy.py tests/integration/test_end_to_end_v2.py
```

Expected: PASS.

- [ ] **Step 8: Benchmark concurrency**

Compare Balanced median processing time on the owner TaiLieuOnThi PDFs for `workers=1` versus auto workers. Keep the concurrent version only when total time improves and all footer/watermark/outside-change metrics remain within gate.

- [ ] **Step 9: Commit**

```bash
git add backend/engine/raster/tdm_guided.py backend/engine/strategies/raster_template.py backend/engine/pipeline_v2/execute.py tests/unit/test_tdm_guided.py tests/unit/test_worker_selection.py tests/integration/test_raster_template_strategy.py tests/integration/test_end_to_end_v2.py
git commit -m "perf: parallelize isolated TaiLieuOnThi page repair"
```

---

### Task 5: Lock preset quality invariants and document performance behavior

**Files:**
- Modify: `tests/frontend/test_frontend_contract.py`
- Modify: `tests/unit/test_worker_selection.py`
- Modify: `README.md`

**Interfaces:** existing preset names and payload values remain unchanged.

- [ ] **Step 1: Add preset invariant tests**

Require the frontend/config contract to preserve:

```text
Fast       -> dpi 200, output_dpi 200, quality 88
Balanced   -> dpi 240, output_dpi 240, quality 92
HighQuality-> dpi 320, output_dpi 320, quality 95
Safe       -> dpi 240, output_dpi 240, quality 95, worker request 1
```

Also assert the default selected preset remains `balanced`, and the new Footer cleanup default remains `auto` for all presets rather than being disabled by Fast.

- [ ] **Step 2: Run preset/worker tests**

```bash
python -m pytest -q tests/frontend/test_frontend_contract.py tests/unit/test_worker_selection.py
```

Expected: PASS after any required test-contract updates from the UI/footer plan.

- [ ] **Step 3: Update README**

Document:

- Fast = speed-sensitive work with existing lower DPI/quality values;
- Balanced = recommended default;
- High Quality = higher fidelity, slower;
- Safe = conservative single-worker mode;
- Footer visual QC remains active for every preset;
- analysis cache applies only to unchanged files within the current app process;
- Auto Smart still owns safe strategy selection unless the user requests a compatibility-gated engine preference.

- [ ] **Step 4: Commit**

```bash
git add tests/frontend/test_frontend_contract.py tests/unit/test_worker_selection.py README.md
git commit -m "docs: lock safe performance preset semantics"
```

---

### Task 6: Final before/after benchmark and release gate

**Files:** no production file is changed by this task unless a benchmark exposes a regression that must be fixed in the owning task.

- [ ] **Step 1: Run full automated verification**

```bash
python -m compileall -q backend desktop_app.py native_api.py
node --check frontend/static/app.js
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run the final benchmark matrix**

For each owner regression PDF and representative synthetic fixture, run Balanced three measured times after one warmup and compare with Task 1 baseline.

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
output_size_bytes
footer_residual_score
watermark_residual_score
outside_change_ratio
```

- [ ] **Step 3: Accept/reject each optimization with explicit gates**

Accept only when median time improves beyond normal run-to-run noise, selected strategy remains correct, all structural/content/footer QC gates pass, output size remains reasonable, and memory/CPU behavior is acceptable. Revert the responsible task commit if it fails these gates.

- [ ] **Step 4: Run Windows/native and Chromium full-stack smoke**

Confirm pywebview window construction/NativeApi binding, frontend primary flow, Auto Smart default, Footer cleanup Auto default, processing to DONE, and populated engine/footer diagnostics.

- [ ] **Step 5: Perform final owner-PDF visual check**

Inspect first, second, dense middle and final pages. No performance change may reintroduce footer ghosts, diagonal watermark residue, damaged formulas/graphs, or altered page numbers.

**Merge gate:** full suite green + full-stack smoke green + quality metrics pass + visual owner regression accepted + measured speed improvement documented.
