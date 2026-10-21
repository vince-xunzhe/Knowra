export const PAPER_CATEGORIES_CHANGED_EVENT = 'knowra:paper-categories-changed'

export function notifyPaperCategoriesChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(PAPER_CATEGORIES_CHANGED_EVENT))
  }
}
