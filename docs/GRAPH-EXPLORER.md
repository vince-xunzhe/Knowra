# Graph tools with the classic canvas

The structured knowledge graph opens with the existing node-and-edge canvas, entity colors, and COSE force layout. The compact Graph tools toggle is collapsed by default. Expanding it exposes topic navigation, local exploration, connection paths, and evidence without replacing the default graph with aggregated topic bubbles. Compiled timelines, concepts, search, candidate review, and the existing node detail drawer remain available.

## Behavior and data boundaries

- **Default canvas:** displays real nodes and their relationships, including similarity links. Clicking a node opens its existing detail drawer and keeps the full graph visible. The original entity legend and hover treatment are retained.
- **Topic directory:** groups visible nodes by their existing category. When the graph endpoint omits category, the UI resolves a majority category from the already loaded source-paper catalog, with deterministic alphabetical tie-breaking. Unresolved nodes stay in Uncategorized. This is presentation grouping, not community detection or reclassification. Topic counts follow the candidate/type scope. Keyboard-accessible inter-topic links list the underlying original relationships.
- **Local exploration:** open a topic or choose a center node, then explore one or two undirected neighbor hops on the classic canvas. The first 80 nodes are displayed with an explicit total and a load-more control. Isolated nodes remain selectable. Search, rescued concepts, and candidate selections enter local exploration automatically. A return-to-all-nodes control remains visible when tools are collapsed.
- **Connections and evidence:** select two visible nodes to find one shortest undirected connection of at most six hops. Original edge directions are retained in the diagram and list. The similarity toggle applies to all exploration scopes and path search; it is enabled initially to preserve the original graph contents. A missing path describes the current filter/depth limits, not absence of a connection in the database.
- **Evidence:** select a real edge to inspect origin, recorded confidence (including zero), evidence excerpts, source paper/field and available extractor version. Multiple provenance contributions remain separate. Missing evidence is explicitly marked; no evidence or confidence is synthesized.
- **Navigation and layout:** preserve dragged positions when changing the relationship filter, cache positions and viewports for up to 30 scopes in the current mount, and support fit, zoom, pin, and back navigation. Opening a drawer adjusts the viewport without rearranging every node. New scopes use the original force layout. Theme changes update the renderer without recreating it. Native controls and node/relationship lists provide keyboard alternatives to canvas interaction.

All projections are derived from the current frontend graph scope. No backend routes, prompts, embeddings, promotion decisions, stored nodes, stored edges, or database schemas are changed. The current category association is not a scientific claim that a concept belongs exclusively to one topic.

## Validation

- `E2E_PORT=4181 npm run test:e2e --prefix frontend -- --workers=2` — 27 Playwright tests plus 5 polling tests.
- `npm run build --prefix frontend`.
- Targeted ESLint on GraphExplorer, KnowledgeGraph, graphExplorer model, the changed tests and Playwright config.
- `git diff --check`.

Regression coverage includes the initial real-node canvas and colors, collapsed tools, preserving the full graph when opening node details, pinning, topic navigation, category resolution, local depth, disconnected nodes, directed provenance on an undirected path, similarity filtering, candidate workflows, language switching, theme updates, and narrow layouts in all four languages. Browser tests use fixture data and intercept API requests.

The full frontend lint command has existing errors outside this implementation, including two pre-existing effect-state errors in GraphPage. The production build retains the existing large-chunk advisory. Backend ML/evaluation commands do not apply to this frontend-only change.

## Follow-ups

Saved layouts currently last for the component mount only. Consider persistence or a dedicated large-graph renderer after profiling and UX review. Neighborhood and path operations use the fully loaded, filtered graph; a future server-paged graph will need matching scope-aware query APIs.
