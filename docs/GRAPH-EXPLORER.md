# Graph explorer: three complementary perspectives

The structured knowledge graph now opens with a topic overview, with local exploration and connection evidence as complementary perspectives. Compiled timelines, concepts, search, candidate review, and the existing node detail drawer remain available.

## Behavior and data boundaries

- **Overview:** aggregate visible nodes by their existing category. The graph endpoint may omit category, so the UI resolves a majority category from the already loaded source-paper catalog, with deterministic alphabetical tie-breaking. Unresolved nodes stay in Uncategorized. This is presentation grouping, not community detection or reclassification. Topic counts include all nodes in the current candidate/type scope. Inter-topic links aggregate original edges; select one to inspect its underlying relationships.
- **Local:** open a topic or select a center node, then explore one or two undirected neighbor hops. The first 80 nodes are displayed with an explicit total and a load-more control. Isolated nodes remain selectable. Search, rescued concepts, and candidate selections enter local exploration automatically.
- **Connections and evidence:** select two visible nodes to find one shortest undirected connection of at most six hops. Original edge directions are retained in the diagram and list. Similarity links are excluded by default, and the toggle applies consistently to all views and path search. A missing path describes the current filter/depth limits; it does not claim there is no connection in the database.
- **Evidence:** select a real edge to inspect origin, recorded confidence (including zero), evidence excerpts, source paper/field and available extractor version. Multiple provenance contributions remain separate. Missing evidence is explicitly marked; no evidence or confidence is synthesized.
- **Reading:** deterministic preset positions replace idle autoplay and continuous force relayout. Dragged positions and viewports are retained across navigation (up to 30 view layouts in the current mount). Fit, zoom, pin, and history controls are available. Theme changes update the renderer without recreating it. Labels reveal with zoom or hover; native controls and lists provide keyboard alternatives to canvas interaction.

All projections are derived from the current frontend graph scope. No backend routes, prompts, embeddings, promotion decisions, stored nodes, stored edges, or database schemas are changed. The current category association is not a scientific claim that a concept belongs exclusively to one topic.

## Validation

- `E2E_PORT=4181 npm run test:e2e --prefix frontend -- --workers=2` — 25 Playwright tests passed, plus 5 polling tests.
- `npm run build --prefix frontend` — passed.
- Targeted ESLint on GraphExplorer, KnowledgeGraph, graphExplorer model, the changed tests and Playwright config.
- `git diff --check`
- Read-only local graph check, with API mutations intercepted during browser inspection: 768 API nodes / 3,306 edges; after existing hidden-type filtering, 766 nodes formed 10 topic groups and 31 structural inter-topic connections. A group expanded to 80 nodes with no browser errors. These counts are a point-in-time UI check, not an algorithm benchmark.

The full frontend lint command has existing errors outside this implementation, including two pre-existing effect-state errors in GraphPage (confirmed against the base revision). The production build retains the existing large-chunk advisory. Backend ML/evaluation commands do not apply to this presentation-only change.

## Follow-ups

Automatically discovered communities and semantic naming can be added as a separately validated grouping option. Persistent saved views and a dedicated large-graph renderer should be considered only after profiling and UX review. The current neighborhood and path operations run against the fully loaded, filtered graph; a future server-paged graph will need matching scope-aware query APIs.
