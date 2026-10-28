import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import DomainPage from '../src/pages/DomainPage'
import '../src/index.css'

export default function HighlightHarness() {
  const [active, setActive] = useState(true)
  const mode = new URLSearchParams(window.location.search).has('roadmap') ? 'roadmap' : 'domain'
  return <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
    <div style={{ height: 30, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 20px', background: '#172033', color: '#a5b4fc', fontSize: 12 }}>
      <span>合成数据验收 · 论文、连线与证据均为测试夹具</span>
      <button onClick={() => setActive(value => !value)}>{active ? '离开合成页面' : '返回合成页面'}</button>
    </div>
    <div hidden={!active} style={{ flex: 1, minHeight: 0 }}>
      <DomainPage active={active} mode={mode} onOpenReview={() => setActive(false)} />
    </div>
  </div>
}

createRoot(document.getElementById('root')!).render(<HighlightHarness />)
