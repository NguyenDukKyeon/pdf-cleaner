# PDF Cleaner V2.1 — Engine UI, Footer Polish, and Safe Performance Design

**Status:** APPROVED by owner on 2026-09-12

## Goal

Improve PDF Cleaner V2 so users can understand which processing engine is used, select a safe processing preference when needed, remove the remaining visual residue in TaiLieuOnThi/TDM footers, and reduce processing time without weakening content-preservation or QC guarantees.

This design extends `2026-09-10-pdf-cleaner-v2-design.md`; Auto-first routing remains the architectural default.

## Evidence and problem statement

The owner-provided TaiLieuOnThi PDFs show the same visual failure mode:

- original/backup pages contain a centered footer URL watermark (`https://TaiLieuOnThi.Net`) plus a pale diagonal watermark near the lower-right;
- the current cleaned output removes the main footer URL text but leaves a faint gray halo/tone residue on some pages;
- legitimate page numbers, the thin footer rule, and colored teacher/slogan text must remain unchanged.

This is therefore a localized post-clean visual-residual problem, not a general watermark-detection failure.

## Root causes

1. **Footer ROI is intentionally tight.** The legacy TDM geometry uses `DEFAULT_BOTTOM_BOX = (0.34, 0.962, 0.72, 0.998)` to avoid page numbers and real footer content. That safety margin can leave anti-aliased fringe outside the strongest cleanup area.
2. **Trusted transfer is conservative.** `tdm_guided.py` only transfers pixels that changed in the 200-DPI working image and lie inside trusted masks. Low-contrast fringe can remain below that threshold.
3. **Low-resolution reconstruction can create a tone seam.** TDM-guided core cleanup is computed on a bounded working image and then transferred back to the native page. Small gray footer glyphs are especially sensitive to resampling mismatch.
4. **QC is not footer-specific.** Current watermark residual scoring can pass while low-contrast unacted fringe remains visible. There is no dedicated post-clean footer residual score or escalation rule.

## Product principles

- Auto Smart is the default.
- Manual engine choices are compatibility-gated preferences, never unchecked destructive dispatch.
- Subject names are not engine names; existing subject wire values remain only as content-protection hints.
- Footer cleanup is local and native-resolution; never whiten the whole bottom strip.
- OCR remains optional.
- No performance change may weaken structural/content/footer QC.
- Owner PDFs are acceptance inputs only and are not committed without explicit approval.

## Engine UI

The main processing section uses descriptive engine cards.

| Engine | Wire value | Purpose | One-line copy |
| --- | --- | --- | --- |
| Auto Smart | `auto_smart` | Analyze representation/watermark evidence and choose the safest/cheapest strategy. | `Tự chọn cách xử lý nhanh và an toàn nhất cho từng PDF.` |
| Stream Clean | `stream_clean` | Remove repeated structural watermark streams/objects without page rasterization. | `Nhanh nhất — giữ nguyên chữ, vector và chất lượng PDF gốc.` |
| Raster Clean | `raster_clean` | Repair watermark on raster/full-page-image PDFs using native image extraction and document/signature models. | `Dành cho PDF scan/ảnh — ưu tiên giữ chi tiết ảnh gốc.` |
| Compatibility Clean | `compatibility_clean` | Explicitly use the legacy compatibility pipeline for difficult/unsupported cases. | `Cho PDF khó hoặc chưa đủ chắc chắn để dùng engine nhanh.` |

### Compatibility behavior

- `auto_smart`: normal router output.
- `stream_clean`: analyzer still runs; processing is allowed only when the computed safe plan is `STREAM_REMOVE`.
- `raster_clean`: analyzer still runs; processing is allowed only when the computed safe plan is `RASTER_TEMPLATE`.
- `compatibility_clean`: explicit Legacy plan.

Incompatible manual choices stop before destructive processing with an actionable message. They do not bypass staging or QC.

### Vector Repair

`vector_remove` exists in router models but has no production default executor. It may appear in diagnostics when Auto plans it, but it is not selectable until its own strategy, tests, QC contract, and benchmark evidence exist.

## Content protection UI

Keep wire values unchanged; change only labels/copy.

| Wire value | New label | Copy |
| --- | --- | --- |
| `auto` | Auto | `Tự cân bằng làm sạch và bảo toàn nội dung.` |
| `math` | Formula & Diagram Safe | `Giữ công thức, bảng, đồ thị và đường kẻ rõ nét.` |
| `physics` | Diagram & Line Safe | `Ưu tiên sơ đồ, hình minh họa và các nét mảnh.` |
| `chemistry` | Symbol & Structure Safe | `Bảo vệ ký hiệu, chỉ số nhỏ và cấu trúc công thức.` |
| `ebook` | Text & Image Safe | `Giữ chữ dài, ảnh, bìa màu và bố cục ebook.` |

These remain router/legacy safety hints; they never directly choose the watermark engine.

## Footer cleanup UI

Advanced setting **Footer cleanup**:

- `Auto` — Standard first, escalate to Deep only when footer residual remains above threshold. Copy: `Tự tăng mức làm sạch nếu chân trang vẫn còn vệt mờ.`
- `Standard` — one conservative native-resolution polish pass. Copy: `Làm sạch nhẹ vùng URL ở chân trang.`
- `Deep` — stronger local residual cleanup with the same protection guards. Copy: `Làm sạch kỹ vệt mờ còn sót, vẫn giữ số trang và nội dung thật.`

Default is `Auto`.

## Diagnostics

Show the actual strategy and cleanup result, not only the requested preference:

- document kind;
- watermark family;
- actual strategy;
- strategy confidence;
- requested engine preference;
- worker count;
- native image pages;
- OCR calls;
- footer cleanup level used;
- footer residual score;
- fallback status/reason.

Example:

```text
Đã chọn: Raster Clean
PDF scan · TaiLieuOnThi · Confidence 96%
Footer cleanup: Deep · residual 0.018
OCR: 0
```

## Footer polish architecture

Create `backend/engine/raster/footer_polish.py`; do not expand `watermaker TDM.py` further.

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

### Native-resolution policy

The existing TDM-guided pass still performs core removal. Footer Polish then works directly on the native-size RGB page produced by that pass. It must not downsample and re-upsample the footer again.

### Target and guards

- Work only inside `footer_url_box`.
- Always exclude `page_number_guard`.
- Always exclude everything above `upper_content_guard_y`.
- Union those guards with any available existing content-protect mask.
- Preserve long legitimate footer rules rather than classifying the entire line as watermark residue.

### Residual mask

Candidate pixels are low-saturation, low-to-medium contrast gray components that differ from a robust local paper/background estimate. Reject protected pixels, broad page texture, the page-number guard, colored content, and legitimate long lines.

### Background reconstruction

Estimate background from nearby unprotected native-resolution pixels and replace/blend only candidate components. Standard uses minimal component growth; Deep may use stronger reconstruction and at most slightly larger component dilation while keeping identical guards.

### Auto escalation

1. Run Standard.
2. Compute `footer_residual_score`.
3. Stop if score `<= 0.035`.
4. Otherwise run Deep.
5. Compute/report final score and cleanup level.

## QC design

Extend `QCReport` with defaulted optional fields:

```python
footer_residual_score: float | None = None
footer_cleanup_level: str | None = None
footer_protected_change_ratio: float | None = None
```

For TaiLieuOnThi/TDM-guided output:

- page count and geometry must pass;
- existing native outside-change and watermark-residual gates must pass;
- `footer_residual_score <= 0.035`;
- `footer_protected_change_ratio <= 0.002`;
- page-number/content guards must remain visually unchanged within the test tolerance.

Footer metrics are optional for non-applicable strategies so Stream Remove/generic paths remain backward compatible.

## Performance design

Performance is implemented in a separate plan so correctness can be reviewed independently.

### P0 — staged analysis

Inspect 3 representative pages first, expand to 5 only if confidence is insufficient, and expand to 8 only when still ambiguous. Existing router confidence bands do not change.

### P1 — process-local analysis cache

Cache typed `DocumentProfile` results only for unchanged files using a conservative fingerprint: size, mtime, and sampled SHA-256 content. No open PyMuPDF documents are cached.

### P1 — TaiLieuOnThi page-worker concurrency

The existing TDM-guided implementation already isolates page repair in short-lived subprocess workers. Run those worker invocations concurrently with a bounded parent-side executor, then assemble pages in deterministic order. Do not share one mutable `fitz.Document` across workers.

### Presets

Keep existing values:

- Fast: 200/200 DPI, quality 88;
- Balanced: 240/240 DPI, quality 92 and default;
- High Quality: 320/320 DPI, quality 95;
- Safe: 240/240 DPI, quality 95, single-worker request.

Footer visual QC remains active for every preset.

## Regression policy

Repository tests use synthetic generated fixtures; owner PDFs remain external acceptance inputs.

Synthetic fixture must include:

- centered gray footer URL-like glyphs;
- colored footer content above the target;
- a page number on the lower-right;
- a legitimate thin horizontal rule;
- optional pale diagonal watermark.

Visual acceptance on owner PDFs:

- no readable/obvious footer URL ghost at normal scale;
- no rectangular white patch, halo edge, or visible tone seam;
- page number unchanged;
- teacher/slogan line unchanged;
- diagonal cleanup does not regress;
- page count and geometry unchanged.

Performance acceptance compares the same input/preset/QC before and after and records analyze, processing, QC and total time; pages/sec; strategy; workers; native image pages; OCR calls; output size; footer residual; watermark residual; and outside-change ratio.

## Expected files

Correctness/UI implementation:

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
backend/engine/raster/footer_polish.py
backend/engine/raster/tdm_guided.py
backend/engine/strategies/raster_template.py
backend/engine/qc_v2/validator.py
tests/frontend/test_frontend_contract.py
tests/unit/test_router.py
tests/unit/test_footer_polish.py
tests/unit/test_tdm_guided.py
tests/unit/test_qc_v2.py
tests/unit/test_native_api_contract.py
tests/integration/test_raster_template_strategy.py
tests/integration/test_end_to_end_v2.py
```

Performance implementation:

```text
scripts/benchmark_v2.py
backend/engine/pipeline_v2/analysis_cache.py
backend/engine/analyzer/document_analyzer.py
backend/engine/pipeline_v2/analyze.py
backend/engine/pipeline_v2/execute.py
backend/engine/raster/tdm_guided.py
backend/engine/strategies/raster_template.py
tests/unit/test_benchmark_v2.py
tests/unit/test_analysis_cache.py
tests/unit/test_document_sampler.py
tests/unit/test_tdm_guided.py
tests/unit/test_worker_selection.py
tests/integration/test_raster_template_strategy.py
tests/integration/test_end_to_end_v2.py
```

## Non-goals

- Do not delete legacy engines.
- Do not expose Vector Repair yet.
- Do not add mandatory OCR.
- Do not route by school subject.
- Do not whiten the entire footer band.
- Do not change legitimate page-number/footer branding.
- Do not accept speed improvements that weaken QC.

## Definition of done

1. Engine preference and content-protection profile are clearly separated in UI with one-line purpose copy.
2. Auto Smart remains default and manual engine preferences are compatibility-gated.
3. TaiLieuOnThi footer residue is visually clean on owner acceptance PDFs without damaging real footer content.
4. Footer cleanup Auto can escalate Standard → Deep and exposes metrics to diagnostics/QC.
5. Full Python suite, JS syntax, Windows/native smoke and Chromium primary-flow smoke are green.
6. Performance changes have before/after benchmark evidence and pass all old/new quality gates.
