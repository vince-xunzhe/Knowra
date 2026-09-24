/** Personal recommendations are owned by the local workspace, not cloud auth. */
import axios from 'axios'
import type { PersonalFeed as CloudFeed, PersonalRecItem as CloudItem } from './cloud'
export interface RecommendationBatch {
  id: string; status: string; error: string | null; slot: string; count: number;
  profile_version: number | null; created_at: string; completed_at: string | null;
}
export interface PersonalRecItem extends CloudItem { in_library: boolean }
export interface PersonalFeed extends CloudFeed {
  batch: RecommendationBatch | null;
  latest_batch: RecommendationBatch | null;
  items: PersonalRecItem[];
}
export interface RecommendationHistory { batches: RecommendationBatch[]; retention_days: number; next_offset: number | null }

const api = axios.create({ baseURL: '/api/recommendations/personal', timeout: 30000 })
export class PersonalRecommendationsUnavailableError extends Error {
  constructor() {
    super('本机推荐服务尚未加载，请重启新版桌面后端。此功能不需要云端登录。')
    this.name = 'PersonalRecommendationsUnavailableError'
  }
}
export async function personalRecommendations(batchId?: string): Promise<PersonalFeed> {
  try { return (await api.get<PersonalFeed>('', { params: { batch_id: batchId || undefined, include_library: true } })).data }
  catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404 && !batchId) throw new PersonalRecommendationsUnavailableError()
    throw error
  }
}
export const saveRecommendationFocus = (current_focus: string) => api.put('/focus', { current_focus }).then(r => r.data)
export const refreshPersonalRecommendations = () => api.post<{ id: string | null; status: string }>('/refresh').then(r => r.data)
export const recommendationEvent = (batch_id: string, arxiv_id: string, kind: 'exposed' | 'viewed' | 'requested') => api.post('/events', { batch_id, arxiv_id, kind }).then(r => r.data)

export const recommendationHistory = (offset = 0) => api.get<RecommendationHistory>('/batches', { params: { offset } }).then(r => r.data)

export interface RecommendationStorage {
  retention_days: number; expired_batches: number; expired_candidates: number;
  protected_batches: number; estimated_payload_bytes: number; database_bytes: number;
}
export const recommendationStorage = () => api.get<RecommendationStorage>('/storage').then(r => r.data)
export const cleanupRecommendationStorage = () => api.post<{ deleted_batches: number; deleted_candidates: number; compacted: boolean; reclaimed_bytes: number }>('/storage/cleanup').then(r => r.data)
