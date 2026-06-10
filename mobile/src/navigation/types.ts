/**
 * Centralized route param types for the stack/tabs.
 *
 * Each nested stack lists the screens it owns. The tab navigator pushes
 * onto these stacks rather than into the tab navigator itself, so the
 * back gesture works without colliding with tab switches.
 */

export type RootStackParamList = {
  // Papers tab stack
  PapersList: undefined
  // Rich per-paper view: structured model extraction (parsed from the
  // paper's raw_llm_response) with a toggle to the compiled wiki .md.
  // We pass paperId (the detail screen reads the row from the snapshot
  // context) plus the optional wiki download URL for the toggle.
  PaperDetail: {
    paperId: string
    title: string
    // The wiki file's id — used to fetch a FRESH signed URL at view
    // time (snapshot download URLs expire after 10 min). Null when the
    // paper has no compiled wiki page.
    wikiFileId?: string | null
  }

  // Concepts tab stack
  ConceptsList: undefined
  WikiDetail: {
    title: string
    rel_path: string
    download_url: string
  }
}
