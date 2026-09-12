# PDF Cleaner V2 — Architectural Refactor Design

**Status:** APPROVED by owner on 2026-09-10

## Goal

Refactor PDF Cleaner from subject-selected, page-level watermark cleaning into a document-level automatic watermark router that preserves PDF structure whenever possible, processes raster-only documents efficiently, exposes clear progress/QC in the desktop UI, and keeps the current legacy engines available as safety fallbacks until V2 proves equivalent or better.

## Why the current architecture must change

The current UI routes by subject (`toan`, `ly`, `hoa`, `ebook`), but watermark representation is independent of subject. The supplied regression documents show the same TaiLieuOnThi family appearing in structurally different PDFs: some are effectively full-page raster images, while another retains a text/vector layer and repeated overlay content. A subject selector therefore cannot reliably choose the cheapest or safest removal strategy.

The current code also has these structural constraints:

- `backend/service.py` hard-codes desktop worker count to one even though the engine already contains RAM-aware worker selection.
- `backend/engine/core_stream.py` contains useful structural stream analysis but is not the primary document router.
- `backend/engine/tdm_cleaner/` is page-oriented and delegates to legacy subject processors.
- `backend/engine/watermaker TDM.py` is a large legacy implementation that should be isolated rather than expanded.
- The UI makes the user select a subject-specific engine instead of defaulting to automatic document analysis.
- There is no regression test suite protecting watermark removal behavior.

## Product behavior

### Default mode

The primary UI defaults to **Auto**. The user selects PDFs and a quality preset; the application analyzes each document and selects a processing strategy automatically.

Subject profiles move to **Advanced → Content protection profile**. They may tune safety behavior for math/diagrams, physics, chemistry, or ebook-like content, but they must not directly choose the watermark engine.

### User-visible stages

Jobs expose these stages:

1. `QUEUED`
2. `ANALYZING`
3. `PLANNING`
4. `PROCESSING`
5. `VERIFYING`
6. `DONE` or `FAILED` / `CANCELLED`

The UI displays strategy, confidence, worker count, whether native image extraction was used, and OCR call count when available.

## Architecture

```text
PDF
 |
 v
DocumentAnalyzer
 |
 v
DocumentProfile
 |
 v
WatermarkRouter
 |
 v
ProcessingPlan
 |
 +----------------+------------------+
 |                |                  |
 v                v                  v
Stream/Object   Vector/Hybrid      Raster
Strategy        Strategy           Strategy
 |                |                  |
 +----------------+------------------+
                  |
                  v
               Executor
                  |
                  v
                 QC
                  |
                  v
               Result/Report
```

### 1. DocumentAnalyzer

The analyzer samples representative pages and returns a typed `DocumentProfile`. It must detect at minimum:

- page count;
- text-layer coverage/character count;
- proportion of pages dominated by a single full-page image;
- repeated content streams and repeated images;
- watermark candidates and confidence evidence;
- likely document representation: `vector`, `hybrid`, or `raster`.

Analysis is document-level. Expensive recognition must not run independently on every page.

### 2. Watermark signature registry

Known watermark families are represented as data rather than hard-coded branches. A signature may contain:

- aliases/markers such as `TAILIEUONTHI.NET`, `TaiLieuOnThi.Net`, `Tài Liệu Ôn Thi Group`;
- typical normalized regions;
- expected repetition;
- rotation or transform hints;
- optional stream/image fingerprints.

A text match is evidence, not proof. The router combines repetition, position, stream/image identity, transform, opacity/color and optional OCR/template evidence.

### 3. WatermarkRouter

The router consumes a `DocumentProfile` and returns a typed `ProcessingPlan`. Initial strategies:

- `stream_remove`: remove isolated repeated content streams / objects without rasterizing;
- `vector_remove`: remove repeated text/path watermark objects when safe;
- `raster_template`: learn a repeated watermark template once per document, then apply it across pages;
- `legacy`: existing subject-specific processing as fallback.

Confidence bands:

- `>= 0.95`: automatic strategy execution;
- `0.70–0.95`: execute with stronger QC / conservative fallback;
- `< 0.70`: use safe legacy fallback or report uncertainty rather than aggressively deleting content.

### 4. Structural fast path

For digital/vector PDFs, prefer direct removal of isolated repeated streams or objects. Do not render a full page if the unwanted overlay can be removed structurally.

Repeated-stream detection should use both exact byte/hash identity and normalized structural fingerprints where practical. Existing `core_stream.py` behavior is reused and progressively moved behind a strategy interface.

### 5. Raster fast path

For pages dominated by one full-page image XObject, extract the native image with PyMuPDF instead of rendering the page again at a lower or different DPI. Only fall back to page rendering when direct image extraction/replacement is unsafe or unsupported.

### 6. Document-level raster watermark learning

For raster PDFs:

1. sample 3–8 representative pages;
2. normalize geometry/orientation;
3. identify repeated watermark candidates through cross-page consensus/template evidence;
4. optionally classify small candidate ROIs with OCR;
5. build one reusable watermark model/template for the document;
6. apply it to pages in parallel.

OCR is an optional classifier/detector, not the primary engine and not a per-page requirement.

For a **known high-confidence watermark signature**, V2 may reuse a proven legacy cleanup primitive as a signature-specific pixel repair implementation without reverting to subject-based routing. For the `tailieuonthi` signature, the approved implementation may use the proven TDM V7 header/footer/diagonal/table/graph cleanup on a bounded working image, then transfer only pixels inside trusted watermark zones back to the native page image. The original subject selector must not decide this path. Unknown raster watermarks continue to use generic document-level consensus/template repair.

Because the TDM V7 implementation retains substantial native state across pages, signature-specific repair must run in short-lived isolated page workers. The parent process owns document routing, cancellation, output assembly, and QC. For conservative full-page raster PDFs, output assembly may rebuild same-geometry pages from cleaned native images; this is not counted as page rasterization because the source was already a full-page raster XObject. Documents with unsafe extra structure must fall back rather than silently discarding it.

### 7. Repair policy

Prefer minimal pixel changes. Where a translucent watermark can be modelled, favor deblending/alpha recovery. Use inpainting only as fallback for damaged regions that cannot be reconstructed safely.

### 8. Legacy isolation

Existing subject engines remain available through adapters during migration:

- TDM / Toán
- IPCLASS / Lý
- TYHH / Hóa
- Ebook

V2 routes to legacy only when confidence is insufficient, V2 execution fails safely, or an explicit advanced compatibility setting requests it. Legacy code is not deleted until regression and benchmark evidence demonstrates V2 coverage.

## Proposed module boundaries

```text
backend/engine/
├── analyzer/
│   ├── __init__.py
│   ├── models.py
│   ├── document_analyzer.py
│   ├── stream_analyzer.py
│   └── raster_analyzer.py
├── signatures/
│   ├── __init__.py
│   ├── registry.py
│   └── tailieuonthi.json
├── router/
│   ├── __init__.py
│   ├── models.py
│   └── router.py
├── strategies/
│   ├── __init__.py
│   ├── base.py
│   ├── stream_remove.py
│   ├── raster_template.py
│   └── legacy.py
├── raster/
│   ├── __init__.py
│   ├── image_extractor.py
│   ├── sampler.py
│   ├── consensus.py
│   ├── text_guard.py
│   └── repair.py
├── pipeline_v2/
│   ├── __init__.py
│   ├── analyze.py
│   ├── plan.py
│   ├── execute.py
│   └── report.py
└── qc_v2/
    ├── __init__.py
    └── validator.py
```

Existing modules remain in place until migration is complete.

## Backend/service design

`backend/service.py` remains a compatibility façade during migration. New responsibilities move into focused modules under `backend/app/`:

```text
backend/app/
├── jobs.py
├── processing_service.py
├── config_service.py
├── output_service.py
└── qc_service.py
```

The pywebview architecture remains. No FastAPI or localhost server is introduced.

`native_api.py` gains compatible calls for document analysis/reporting while preserving existing file dialogs and job polling.

## Parallelism

Remove the desktop hard-code that forces one CPU worker. Worker selection defaults to `auto_worker_count()` and remains bounded by page count, logical CPUs, memory estimation and any runtime hard cap.

Document analysis/template learning occurs once. Parallel workers process pages only after the document-level model/plan exists; workers must not repeat expensive analysis.

## Quality control

V2 QC is multi-layered:

### Structural checks

- output opens successfully;
- page count unchanged;
- page geometry preserved within tolerance.

### Text/vector preservation

When a meaningful text layer exists, compare extracted text before/after and reject unexpected loss outside targeted objects.

### Raster preservation

Measure change outside the predicted watermark mask. Unrelated content must remain substantially unchanged.

### Watermark residual

Compare candidate/template evidence before and after processing. Residual watermark confidence should fall while content-loss metrics stay within limits.

### Atomic output

Current staging, backup-on-overwrite and promotion semantics remain. Failed QC must never replace the source/final output.

## Frontend design

### Primary workflow

Replace subject cards with:

- `Auto` as default processing mode;
- quality preset: Fast / Balanced / High Quality / Safe;
- analyze/process action;
- automatic analysis summary containing representation, detected watermark family, chosen strategy and confidence.

### Advanced workflow

Keep content safety profiles under Advanced:

- Auto
- Math / diagrams
- Physics
- Chemistry
- Ebook

These are hints only. They do not hard-route to a watermark engine.

### Progress

Show stage-based progress:

```text
Analyze PDF       ✓
Detect watermark  ✓
Process           12 / 20
Verify quality    …
```

Expose useful diagnostics without requiring the user to understand internals.

## Regression corpus

The three supplied owner documents form the initial local regression corpus and are **not committed to the public repository unless the owner explicitly requests it**.

Expected routing:

1. chemistry balance document: raster path / raster-template candidate;
2. TDM sequence document: raster path / raster-template candidate;
3. oscillation document: vector/hybrid structural path; avoid full-page rasterization when the TaiLieuOnThi overlay can be isolated.

Tests may generate synthetic PDFs that reproduce these structural cases so CI does not depend on copyrighted/private fixtures.

## Testing strategy

Use TDD for every behavior change.

- unit tests: analyzer metrics, signature registry, router decisions, worker selection, QC calculations;
- integration tests: synthetic vector-overlay PDF, full-page-raster PDF, mixed/hybrid PDF;
- frontend tests: payload construction and event-stage rendering where practical;
- regression benchmark: local owner fixtures compare legacy vs V2 speed, watermark residual, content preservation and output validity.

## Migration policy

No big-bang rewrite. Each phase must leave the application runnable. New behavior is introduced behind typed interfaces and compatibility façades. Legacy deletion is a final, evidence-based cleanup task only.

## Acceptance criteria

- Auto is the default user workflow.
- Subject/content profile no longer chooses the engine directly.
- Vector/structural watermarks are removed without full-page rasterization when safely isolated.
- Raster documents can use direct native-image extraction when safe.
- Raster watermark learning is document-level rather than repeated per page.
- OCR is lazy/optional and limited to candidate ROIs.
- Worker selection can use multiple processes safely instead of being hard-coded to one.
- Cancellation, staging, backup and atomic promotion remain correct.
- V2 QC checks structure, content preservation and watermark residual.
- Synthetic CI regression tests cover vector, raster and hybrid routes.
- The three supplied owner documents pass local regression checks before legacy removal.
- Legacy engines remain until benchmark/regression evidence supports removal.
