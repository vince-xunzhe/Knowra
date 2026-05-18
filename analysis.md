# VIN-12 Frontend Observability Improvements

## Changed files
- frontend/src/components/TaskNotice.tsx
- frontend/src/components/PaperProcessBadge.tsx
- frontend/src/pages/GraphPage.tsx
- frontend/src/pages/PapersPage.tsx
- frontend/src/pages/ReviewPage.tsx
- frontend/src/pages/SettingsPage.tsx
- frontend/src/components/AskDrawer.tsx
- artifacts/reviews/VIN-12/impact_report.json
- artifacts/reviews/VIN-12/impact_report.md
- artifacts/reviews/VIN-12/build_output.txt

## Commands run
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-12/frontend && npm --version && npm run build`

## Test / eval result
- Build command passed in issue worktree frontend path: `/Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-12/frontend`
- Result: `tsc -b && vite build` success.
- Notes: Vite emitted existing chunk-size warning (>500kB), no compilation/type errors.

## Metric delta (qualitative)
- Papers/Review now expose structured paper lifecycle stages (`待处理 / 处理中 / 已处理 / 失败`) with explicit stage summaries.
- Papers and Review surface recent failure summaries in page context, reducing first-pass troubleshooting cost.
- Graph/Settings/Ask key operations now use a unified non-blocking notice pattern with success/failure detail and retry CTA.

## Risks and follow-up
- `status.current === filename` is still used for per-paper `处理中` inference; if backend status wording changes, running-stage mapping may drift.
- Current metric delta is qualitative only; recommend adding telemetry counters (`retry` click-through, failure recovery time) to quantify reduction in manual troubleshooting time.
