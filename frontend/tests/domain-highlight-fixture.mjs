// Public synthetic fixture only; no user titles, evidence, identifiers or images.
export function createHighlightFixture() {
  const titles = [
    'Synthetic foundations of prediction', 'Synthetic structured world model',
    'Synthetic imagination for planning', 'Synthetic recurrent dynamics',
    'Synthetic evaluation protocol', 'Synthetic alternate objective',
    'Synthetic isolated reference',
  ]
  const papers = titles.map((title, index) => ({ id: `synthetic-paper-${index}`, title, year: String(2015 + index),
    category: '合成连线图', sections: { core_contribution: '仅供隔离验收的合成贡献。', abstract_summary: '合成摘要。', problem: '合成问题。', motivation: '合成动机。' },
    eligible: true, reason: null, incomplete: false }))
  const coordinates = [[60, 180], [420, 70], [780, 180], [420, 400], [780, 600], [60, 600], [420, 700]]
  const nodes = papers.map((paper, index) => ({ id: `n${index}`, kind: 'paper', paperId: paper.id,
    x: coordinates[index][0], y: coordinates[index][1], width: 280, height: 160,
    text: index === 3 ? '合成锁定节点：位置与备注必须保留。' : '', groupId: null, locked: index === 3 }))
  const edges = [[0, 1], [1, 2], [1, 3], [2, 4], [5, 0], [3, 1]].map(([source, target], index) => ({
    id: `e${index}`, source: `n${source}`, target: `n${target}`,
    label: ['基于', '改进', '人工对照', '评估', '对比', '反馈'][index], locked: index === 2,
    analysis: index === 2 ? null : { relation: 'compares', certainty: 'inferred',
      reason: `合成证据 ${index}；仅用于检查查看证据的交互。`,
      evidence: [{ paperId: `synthetic-paper-${source}`, source: 'review', locator: 'Synthetic section', page: null,
        quote: 'Synthetic evidence only; no real research content.' }] },
  }))
  return { revision: 3, papers, state: { activeId: 'highlight', boards: [
    { id: 'highlight', name: '合成连线图', category: '合成连线图', nodes, edges, viewport: { x: 65, y: 35, zoom: 0.88 } },
    // Reusing the selected ID checks that UI focus is scoped to a domain, not just an ID.
    { id: 'other', name: '另一个合成领域', category: '另一个合成领域',
      nodes: [{ ...nodes[1], x: 100, y: 150 }, { ...nodes[2], x: 500, y: 150 }],
      edges: [{ ...edges[1], id: 'other-edge' }], viewport: { x: 0, y: 0, zoom: 1 } },
  ] } }
}
