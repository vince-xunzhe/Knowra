import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import DomainPage from '../src/pages/DomainPage'
import '../src/index.css'

export default function LayoutHarness() {
  const [active, setActive] = useState(true)
  return <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
    <div style={{ height: 30, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 20px', background: '#172033', color: '#a5b4fc', fontSize: 12 }}>
      <span>合成数据验收 · 论文、关系与备注均为测试夹具</span>
      <button onClick={() => setActive(value => !value)}>{active ? '离开合成页面' : '返回合成页面'}</button>
    </div>
    <div hidden={!active} style={{ flex: 1, minHeight: 0 }}>
      <DomainPage active={active} onOpenReview={() => setActive(false)} />
    </div>
  </div>
}

createRoot(document.getElementById('root')!).render(<LayoutHarness />)
