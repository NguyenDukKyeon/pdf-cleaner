# PDF Cleaner V2.1 — Engine UI, Footer Polish, and Safe Performance Design

**Status:** APPROVED by owner on 2026-09-12

## Goal

Improve PDF Cleaner V2 so users can understand which processing engine is used, select a safe processing preference when needed, remove the remaining visual residue in TaiLieuOnThi/TDM footers, and reduce processing time without weakening content-preservation or QC guarantees.

This design extends the approved `2026-09-10-pdf-cleaner-v2-design.md`. It does not replace the Auto-first router.

## Evidence and problem statement

The owner-provided TaiLieuOnThi regression documents show a consistent pattern:

- the original/backup PDF contains a footer URL watermark (`https://TaiLieuOnThi.Net`) in the center-bottom region and a pale diagonal watermark near the lower-right corner;
- the current cleaned result removes the main footer URL text, but a faint gray residual/halo remains visible in the footer region on some pages;
- legitimate footer content such as page numbers and the colored teacher/slogan line must remain unchanged.

The visual defect is therefore not a general failure to remove the watermark. It is a localized residual-cleanup problem after the main TDM-guided repair succeeds.

## Root-cause analysis

### 1. Footer ROI is intentionally tight

The mature TDM engine uses a narrow normalized bottom box (`DEFAULT_BOTTOM_BOX = (0.34, 0.962, 0.72, 0.998)`) to avoid damaging the page-number region and legitimate footer text. This conservative geometry removes the core watermark but can miss anti-aliased fringe near the edges of the watermark glyphs.

### 2. Trusted transfer is intentionally conservative

`tdm_guided.py` transfers only pixels that changed in the 200-DPI working image and also fall inside trusted header/footer/diagonal masks. Low-contrast residue that does not cross the working-image change threshold can survive even when it is visible at native resolution.

### 3. Working-image resampling can create slight tone mismatch

The TaiLieuOnThi-guided path performs cleanup on a bounded 200-DPI working image, upsamples the cleaned working image with cubic interpolation, then transfers trusted pixels back to the native page image. This is safe for preserving native content outside the mask, but it can create a subtle off-white patch, ringing, or halo around very small gray footer glyphs.

### 4. Current QC measures technical residual, not footer visual cleanliness

The current `watermark_residual_score` focuses on candidate pixels that the cleanup actually changed. It can pass while low-contrast fringe remains outside the acted set. There is no footer-specific post-clean visual score or escalation rule.

## Product principles

1. **Auto-first remains the default.** The application must still analyze document structure before choosing a strategy.
2. **Manual engine selection is constrained, not destructive.** A requested engine still passes through compatibility checks. Unsafe combinations are blocked with a clear explanation rather than silently deleting content.
3. **Subject names are not engine names.** Math/Physics/Chemistry/Ebook remain wire-compatible content-protection hints, but the UI labels describe what content is protected rather than school subjects.
4. **Footer cleanup is local.** Never solve the residual by whitening the entire bottom band or rasterizing an otherwise structural PDF.
5. **Quality gates precede speed claims.** Performance changes must preserve existing page count, geometry, content-protection metrics, and new footer-visual metrics.
6. **OCR remains optional.** No mandatory per-page OCR is introduced.

## UI design

### Primary processing engine section

Replace the current implied Auto-only presentation with an engine preference picker. The default is still Auto Smart.

#### Auto Smart — recommended

- **Purpose:** analyze document representation and watermark evidence, then choose the safest/cheapest strategy.
- **One-line copy:** `Tự chọn cách xử lý nhanh và an toàn nhất cho từng PDF.`
- **Wire value:** `auto_smart`

#### Stream Clean

- **Purpose:** remove repeated structural watermark streams/objects without page rasterization.
- **One-line copy:** `Nhanh nhất — giữ nguyên chữ, vector và chất lượng PDF gốc.`
- **Wire value:** `stream_clean`
- **Compatibility gate:** only allowed when analyzer evidence supports a repeated structural watermark at the configured safety threshold.

#### Raster Clean

- **Purpose:** repair watermark on raster/full-page-image PDFs using native image extraction and document/signature-level models.
- **One-line copy:** `Dành cho PDF scan/ảnh — ưu tiên giữ chi tiết ảnh gốc.`
- **Wire value:** `raster_clean`
- **Compatibility gate:** only allowed for conservative raster/full-page-image documents that pass the existing native-image safety checks.

#### Compatibility Clean

- **Purpose:** explicitly use the legacy compatibility pipeline for difficult or unsupported cases.
- **One-line copy:** `Cho PDF khó hoặc chưa đủ chắc chắn để dùng engine nhanh.`
- **Wire value:** `compatibility_clean`

### Vector Repair visibility

`vector_remove` exists in router models, but there is no production V2 default executor implementation yet. It must not be exposed as a selectable engine until the implementation has its own tests, QC contract, and benchmark evidence.

Auto Smart may continue to plan `vector_remove`; until a production strategy exists, the current safe fallback semantics remain.

### Engine selection behavior

The selected engine is a **processing preference with a compatibility gate**:

- `auto_smart`: use normal V2 router output;
- `stream_clean`: analyzer still runs, then the plan must be compatible with Stream Remove or the job stops before destructive processing with an actionable incompatibility message;
- `raster_clean`: analyzer still runs, then the plan must be compatible with Raster Template/TDM-guided processing;
- `compatibility_clean`: explicitly construct/use Legacy strategy.

No manual choice may bypass analyzer safety checks, output staging, or QC.

### Content protection section

Keep existing wire values for backward compatibility:

- `auto`
- `math`
- `physics`
- `chemistry`
- `ebook`

Only the user-facing labels change:

| Wire value | New label | One-line copy |
| --- | --- | --- |
| `auto` | Auto | `Tự cân bằng làm sạch và bảo toàn nội dung.` |
| `math` | Formula & Diagram Safe | `Giữ công thức, bảng, đồ thị và đường kẻ rõ nét.` |
| `physics` | Diagram & Line Safe | `Ưu tiên sơ đồ, hình minh họa và các nét mảnh.` |
| `chemistry` | Symbol & Structure Safe | `Bảo vệ ký hiệu, chỉ số nhỏ và cấu trúc công thức.` |
| `ebook` | Text & Image Safe | `Giữ chữ dài, ảnh, bìa màu và bố cục ebook.` |

These remain safety hints. They must not directly choose the watermark engine.

### Footer cleanup control

Add an advanced setting named **Footer cleanup** with:

- `Auto` — recommended; run Standard first and escalate to Deep only if footer residual exceeds the visual threshold;
- `Standard` — one conservative footer-polish pass;
- `Deep` — stronger local cleanup inside the trusted footer URL ROI, with the same content guards;

Internal/debug-only `off` may exist for tests but is not required in normal UI.

Suggested copy:

- Auto: `Tự tăng mức làm sạch nếu chân trang vẫn còn vệt mờ.`
- Standard: `Làm sạch nhẹ vùng URL ở chân trang.`
- Deep: `Làm sạch kỹ vệt mờ còn sót, vẫn giữ số trang và nội dung thật.`

### Diagnostics

After planning, the UI must display the actual selected strategy, not only the requested preference:

- Engine/strategy name;
- document kind;
- watermark family;
- strategy confidence;
- worker count;
- native image pages;
- OCR calls;
- footer cleanup level actually used;
- footer residual score when available;
- fallback status/reason when applicable.

Example:

```text
Đã chọn: Raster Clean
PDF scan · TaiLieuOnThi · Confidence 96%
Footer cleanup: Deep · residual 0.018
OCR: 0
```

## Footer polish architecture

### New focused module

Create `backend/engine/raster/footer_polish.py` so footer-specific visual cleanup does not expand `watermaker TDM.py` further.

Proposed public interfaces:

```python
from dataclasses import dataclass
from typing import Literal

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


def score_footer_residual(rgb: np.ndarray, *, config: FooterPolishConfig) -> float: ...

def polish_footer_residual(
    native_rgb: np.ndarray,
    *,
    config: FooterPolishConfig,
    content_protect_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, FooterPolishMetrics]: ...
```

Exact thresholds may be calibrated during regression work, but the contract is fixed: the score must measure post-clean visual residue in the footer URL ROI while excluding protected content.

### Footer target region

Use a dedicated footer URL ROI rather than expanding the full bottom strip. The ROI should cover the known centered TaiLieuOnThi URL and its anti-aliased fringe while explicitly excluding the right-side page-number guard.

The colored slogan/teacher line above the URL is protected by the upper-content guard and the existing content-protection mask.

### Residual candidate mask

Within the footer URL ROI, residual candidates are low-saturation, low-to-medium contrast gray components that:

- differ from the local paper/background estimate;
- are not protected dark text, colored ink, page number, or long legitimate footer rule;
- are spatially compact/text-like rather than broad page texture;
- are connected to or near the known footer watermark zone.

The pass must not globally threshold the entire footer.

### Background reconstruction

Prefer native-resolution local background reconstruction for the footer polish itself. Do not upsample a whole 200-DPI cleaned footer patch back into the native image.

Recommended behavior:

1. use the existing TDM-guided pass for core removal;
2. construct a native-resolution footer residual mask;
3. estimate local background from pixels around each candidate component while excluding content-protect masks;
4. replace/blend only candidate pixels;
5. feather one or two pixels only when required to avoid hard mask boundaries.

This specifically addresses the visible tone mismatch caused by transferring a low-resolution cleaned patch.

### Auto escalation

`auto` executes:

1. Standard footer polish;
2. compute `footer_residual_score`;
3. if score `<= residual_threshold`, stop;
4. otherwise execute Deep on the same native page;
5. compute final score and report both before/after values.

Deep must not relax the page-number/content guards. It may use a higher ROI working DPI, slightly larger residual-mask dilation, and stronger local-background reconstruction.

## QC design

Extend `QCReport` with:

```python
footer_residual_score: float | None = None
footer_cleanup_level: str | None = None
footer_protected_change_ratio: float | None = None
```

For TaiLieuOnThi/TDM-guided raster output:

- existing page count and geometry checks remain mandatory;
- existing `native_outside_change_ratio` and watermark residual checks remain mandatory;
- `footer_residual_score` must be below the configured maximum;
- change inside protected footer content must stay below a strict threshold;
- the page-number guard must remain unchanged within the normal visual-diff tolerance.

QC must fail before final output promotion if footer cleanup removes legitimate footer content or leaves visible residue above the threshold.

## Performance design

Performance work is intentionally separated from footer correctness implementation.

### P0: adaptive document sampling

Current analyzer samples up to eight deterministic pages. Change to staged sampling:

1. analyze three representative pages;
2. if representation/watermark evidence is high confidence, stop;
3. otherwise expand to five;
4. expand to eight only when needed.

Do not weaken confidence thresholds to obtain a speed win.

### P0: preset semantics

Keep the existing quality presets but make their intent explicit:

- Fast: analyzer may stop earlier when confidence is already high; legacy/raster working DPI may use the existing lower Fast value;
- Balanced: current default quality/speed target;
- High Quality: higher working/output DPI and full QC;
- Safe: conservative behavior and one worker where already defined.

Footer visual QC still applies to every preset. Fast may not disable footer residual QC.

### P1: cache document analysis

Cache deterministic analysis results by a fingerprint containing at minimum file size, modification time, and a content hash/sample hash. Cache invalidation must be conservative.

### P1: raster page parallelism

Generic raster/template CPU repair may run in parallel only after document-level model learning completes. PyMuPDF document mutation and final PDF assembly remain serialized in the parent process/thread.

The existing TaiLieuOnThi-guided path already uses isolated short-lived page workers; optimize worker scheduling rather than sharing a mutable `fitz.Document` across processes.

### P1: skip unchanged/reused pages

Reuse existing XObject deduplication and add page-skip logic when evidence shows a page does not contain the target watermark. Avoid unnecessary PNG encode/replace operations.

## Regression and benchmark policy

### Owner PDFs

The owner-provided PDFs are acceptance/benchmark inputs but must not be committed to the repository unless the owner explicitly requests it. Local or CI-secret fixture paths may be used during manual verification.

### Synthetic repository fixtures

Add small generated fixtures that reproduce:

- centered gray footer URL watermark;
- legitimate colored footer text above it;
- page number on the lower right;
- a thin horizontal footer rule;
- optional pale diagonal watermark crossing the lower-right quadrant.

These fixtures make unit/integration tests deterministic and distributable.

### Visual acceptance criteria

For the supplied TaiLieuOnThi cleaned output:

- no readable or obvious gray ghost of the removed footer URL at normal viewing scale;
- page number remains visually unchanged;
- teacher/slogan line remains visually unchanged;
- no rectangular white patch, halo edge, or visible tone discontinuity is introduced;
- diagonal cleanup quality must not regress;
- page count and geometry remain identical.

### Performance acceptance criteria

Benchmarks compare the same inputs, same preset, and same output-quality gates before/after optimization.

Report at least:

- analyze seconds;
- processing seconds;
- QC seconds when available;
- total seconds;
- pages/sec;
- selected strategy;
- worker count;
- rasterized/native-image page counts;
- OCR calls;
- output size;
- footer residual score;
- existing watermark residual score;
- outside-change ratio.

A speed change is accepted only if all applicable correctness/QC thresholds still pass.

## Files expected to change

Correctness/UI plan:

```text
frontend/index.html
frontend/static/app.js
frontend/static/styles.css
native_api.py
backend/service.py
backend/engine/router/models.py
backend/engine/router/router.py
backend/engine/pipeline_v2/execute.py
backend/engine/pipeline_v2/report.py
backend/engine/raster/footer_polish.py                 # new
backend/engine/raster/tdm_guided.py
backend/engine/strategies/raster_template.py
backend/engine/qc_v2/validator.py
tests/frontend/test_frontend_contract.py
tests/unit/test_router.py or current router unit-test file
tests/unit/test_footer_polish.py                       # new
tests/unit/test_qc_v2.py or current QC unit-test file
tests/integration/test_end_to_end_v2.py
```

Performance plan may additionally touch:

```text
backend/engine/analyzer/document_analyzer.py
backend/engine/raster/sampler.py
backend/engine/strategies/raster_template.py
backend/engine/pipeline_v2/execute.py
backend/service.py
tests/unit/test_document_analyzer.py
performance/benchmark scripts or existing benchmark harness
```

## Non-goals

- Do not delete legacy engines in this change.
- Do not expose `Vector Repair` until a production implementation exists.
- Do not add mandatory OCR.
- Do not route by school subject.
- Do not whiten the entire footer band.
- Do not alter page numbers or legitimate footer branding.
- Do not merge performance changes that improve timing by weakening QC.

## Definition of done

The feature is done when:

1. UI clearly separates engine preference from content-protection profile and includes one-line purpose copy for every visible option.
2. Auto Smart remains default and all manual engine preferences are compatibility-gated.
3. TaiLieuOnThi/TDM footer URL residue is visually clean on owner acceptance PDFs without damaging page numbers or legitimate footer content.
4. Footer-specific metrics appear in processing report/QC and can trigger Auto escalation from Standard to Deep.
5. Existing full test suite and desktop/frontend smoke tests remain green.
6. Performance optimizations have before/after benchmark evidence and do not regress new or existing QC thresholds.
