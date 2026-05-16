# VIN-12 Frontend Observability Improvements

## Changed files
- frontend/src/components/TaskNotice.tsx
- frontend/src/components/PaperProcessBadge.tsx
- frontend/src/pages/GraphPage.tsx
- frontend/src/pages/PapersPage.tsx
- frontend/src/pages/ReviewPage.tsx
- frontend/src/pages/SettingsPage.tsx
- frontend/src/components/AskDrawer.tsx

## Commands run
- `npm --version && npm run build` (first run failed due missing local dependencies in isolated worktree)
- `npm install`
- `npm --version && npm run build`
- `npm run build` (after Graph page integration)

## Test / eval result
- Build command passed in isolated issue worktree frontend path: `/Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-12/frontend`
- Result: `tsc -b && vite build` success.
- Notes: Vite emitted existing chunk-size warning (>500kB), no compilation/type errors.

## Metric delta (qualitative)
- Papers/Review now expose structured paper lifecycle stages (`待处理 / 处理中 / 已处理 / 失败`) with explicit stage summaries.
- Papers and Review now surface recent failure summaries directly in page context, reducing need to inspect backend logs for first-pass diagnosis.
- Graph/Settings/Ask key operations now use a unified non-blocking notice pattern with success/failure detail and retry CTA, reducing black-box action feedback.

## Risks and follow-up
- `status.current === filename` is still used for per-paper "处理中" inference; if backend status wording changes, running-stage mapping could become less precise.
- Current metric delta is qualitative only; recommend adding telemetry counters (retry clicks, time-to-resolution for failed papers) to quantify reduction in manual troubleshooting time.
