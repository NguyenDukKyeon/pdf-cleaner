# PDF Cleaner Repository Execution Rules

These rules apply to all refactor work on this repository.

1. **Never implement directly on `main`.** Use an isolated feature branch / worktree.
2. **Before every implementation task:** re-read the approved design and the current implementation plan from GitHub. Do not rely on memory or an earlier copy.
3. **TDD is mandatory for behavior changes:** add a failing test, run it and confirm the expected failure, implement the minimum change, then run the focused test and the relevant regression suite.
4. **After every implementation change:** update the plan's task checkbox/status and append verification evidence to its Execution Log in the same logical checkpoint. A task is not complete until the plan reflects the current repository state.
5. **Small commits:** each commit should represent one independently reviewable task/checkpoint. Do not mix unrelated refactors.
6. **Verification before claims:** do not say a task is fixed/complete without current command output or GitHub CI evidence.
7. **Stop on blockers:** if the plan is ambiguous, a required test repeatedly fails, or assumptions about a PDF format are disproved, stop implementation and update/review the plan before proceeding.
8. **No big-bang deletion:** legacy watermark engines remain until V2 regression and benchmark evidence proves replacement coverage.
9. **Do not commit owner-provided regression PDFs** unless the owner explicitly requests it. CI must use generated/synthetic fixtures.
10. **Preserve desktop architecture:** pywebview remains the UI bridge; do not introduce FastAPI/localhost unless the approved design is amended.
11. **Preserve safety semantics:** staging output, cancellation, backup-on-overwrite and atomic promotion must remain regression-tested.
12. **Plan is authoritative:** `docs/superpowers/plans/2026-09-10-pdf-cleaner-v2.md` is the live execution ledger. If implementation changes the approach, amend the design/plan first and record why.
