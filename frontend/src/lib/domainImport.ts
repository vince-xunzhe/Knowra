import type { DomainBoard, DomainPaper } from '../api/domains'

/** The picker references local Paper records into this board, not an external bibliography. */
export function importedPaperIds(board: DomainBoard | null): Set<string> {
  return new Set(board?.nodes.flatMap(node =>
    node.kind === 'paper' && node.paperId ? [node.paperId] : [],
  ) ?? [])
}

export function availableImportPapers(papers: DomainPaper[], board: DomainBoard | null): DomainPaper[] {
  const seen = importedPaperIds(board)
  return papers.filter(paper => {
    // Paper.id remains the identity even when titles match, IDs have no DOI/arXiv
    // metadata, or the library review changes after a card was added.
    if (!paper.eligible || seen.has(paper.id)) return false
    seen.add(paper.id)
    return true
  })
}
