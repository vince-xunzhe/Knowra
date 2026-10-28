import capabilities from '../../../cloud-capabilities.json'
import { t as tr } from '../i18n/catalog'

// Repository policy is shared with the backend. Browser storage and environment
// configuration cannot enable cloud access while this capability is paused.
export function isCloudEnabled(): boolean {
  return capabilities.enabled === true
}

export const CLOUD_PAUSED_TITLE = '云同步与移动端暂时暂停'
export const CLOUD_PAUSED_MESSAGE = '本机论文、知识库、Ask 和推荐精选可继续使用。云同步、完整推荐与移动端暂时暂停。'

export class CloudDisabledError extends Error {
  readonly code = 'cloud_disabled'

  constructor() {
    super(tr(CLOUD_PAUSED_TITLE))
    this.name = 'CloudDisabledError'
  }
}

export function assertCloudEnabled(): void {
  if (!isCloudEnabled()) throw new CloudDisabledError()
}
