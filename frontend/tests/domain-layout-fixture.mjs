// Entirely synthetic: no titles, evidence or identifiers come from user data.
export function createLayoutFixture() {
  const titles = [
    'Synthetic foundations of latent planning', 'Synthetic structured state transitions',
    'Synthetic visual predictive representations', 'Synthetic recurrent dynamics for planning',
    'Synthetic action conditioned imagination', 'Synthetic representation alignment',
    'Synthetic multi-step prediction with uncertainty', 'Synthetic object-centric state estimation',
    'Synthetic consistency under partial observation', 'Synthetic temporal abstraction',
    'Synthetic transfer across simulated environments', 'Synthetic controllable visual generation',
    'Synthetic hierarchy of predictive objectives', 'Synthetic compositional transition models',
    'Synthetic long-horizon reasoning with structured predictive representations and uncertainty estimates for partially observed tasks',
    'Synthetic efficient adaptation of dynamics', 'Synthetic comparison of prediction objectives',
    'Synthetic robust evaluation under distribution shift', 'Synthetic alternate branch baseline',
    'Synthetic alternate branch extension', 'Synthetic curated group study A',
    'Synthetic curated group study B', 'Synthetic manually pinned reference',
  ]
  const papers = titles.map((title, index) => ({ id: `paper-${index}`, title, year: String(2012 + index % 15),
    category: '合成关系图', sections: { core_contribution: '仅供隔离验收的合成贡献。', abstract_summary: '合成摘要。', problem: '合成问题。', motivation: '合成动机。' },
    eligible: true, reason: null, incomplete: false }))
  const nodes = papers.map((paper, i) => ({ id: `n${i}`, kind: 'paper', paperId: paper.id,
    x: 70 + i % 6 * 320, y: 120 + Math.floor(i / 6) * 145,
    width: 280, height: 160, text: i === 6 ? '人工备注：保留该比较的限定条件。' : '', groupId: null, locked: i === 22 }))
  // A deliberate overlap and crossed ordering make the layout regression visible.
  nodes[9].x = nodes[3].x + 80; nodes[9].y = nodes[3].y + 65
  nodes[20] = { ...nodes[20], x: 100, y: 990, groupId: 'curated' }
  nodes[21] = { ...nodes[21], x: 420, y: 990, groupId: 'curated' }
  nodes[22] = { ...nodes[22], x: 930, y: 970 }
  nodes.push({ id: 'curated', kind: 'group', paperId: null, x: 60, y: 930, width: 690, height: 280,
    text: '手工分组 · 原位置保留', groupId: null, locked: true })
  nodes.push({ id: 'note', kind: 'text', paperId: null, x: 1330, y: 970, width: 280, height: 150,
    text: '合成研究备忘\n整理仅移动未分组、未锁定的论文。\n所有关系和证据必须保留。', groupId: null, locked: false })
  const pairs = [[0, 4], [0, 5], [0, 6], [0, 7], [0, 9], [0, 12], [1, 4], [1, 6], [1, 8], [1, 11],
    [2, 5], [2, 7], [2, 8], [2, 10], [2, 13], [3, 6], [3, 8], [3, 11], [4, 9], [4, 10], [5, 9], [5, 11],
    [6, 10], [6, 12], [7, 11], [7, 13], [8, 12], [8, 13], [9, 14], [10, 14], [10, 15], [11, 15], [11, 16],
    [12, 16], [13, 16], [14, 17], [15, 17], [16, 17], [16, 13], [18, 19], [20, 21], [22, 15]]
  const edges = pairs.map(([source, target], i) => ({ id: `e${i}`, source: `n${source}`, target: `n${target}`,
    label: i === 3 ? '人工对照' : ['基于', '对比', '引用'][i % 3], locked: i === 3,
    analysis: i === 3 ? null : { relation: 'compares', certainty: 'inferred', reason: `合成证据 ${i}；不代表真实论文关系。`,
      evidence: [{ paperId: `paper-${source}`, source: 'review', locator: 'Synthetic section', page: null, quote: 'Synthetic evidence only.' }] } }))
  return { revision: 7, state: { activeId: 'dense', boards: [
    { id: 'dense', name: '合成关系图', category: '合成关系图', nodes, edges, viewport: { x: 30, y: 60, zoom: 0.62 } },
    { id: 'other', name: '另一个合成领域', category: '另一个合成领域',
      nodes: [{ ...nodes[0], id: 'other-node', x: 120, y: 120 }], edges: [], viewport: { x: 0, y: 0, zoom: 1 } },
  ] }, papers }
}

/** Crowded outer channels: long skips, reverse/cyclic and parallel relations. */
export function createSpacingFixture() {
  const fixture = createLayoutFixture()
  const board = fixture.state.boards.find(item => item.id === 'dense')
  const pairs = [[0, 14], [0, 15], [0, 16], [0, 17], [1, 14], [1, 15], [1, 16], [1, 17],
    [2, 14], [2, 15], [2, 16], [2, 17], [3, 14], [3, 15], [3, 16], [3, 17],
    [4, 16], [4, 17], [5, 16], [5, 17], [6, 17], [7, 17], [8, 17], [9, 17],
    [16, 13], [16, 13], [13, 16], [22, 0], [22, 4], [20, 17], [21, 12], [17, 22]]
  board.edges.push(...pairs.map(([source, target], index) => ({
    id: `spacing-${index}`, source: `n${source}`, target: `n${target}`, locked: index === 25,
    label: index % 6 === 0 ? `合成长跨层对照关系 ${index} · 保留限定条件与证据` : `合成关系 ${index}`,
    analysis: null,
  })))
  return fixture
}
