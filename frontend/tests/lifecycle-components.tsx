import { useState } from 'react'
import AskDrawer from '../src/components/AskDrawer'
import RejectedRescueModal from '../src/components/RejectedRescueModal'

export function AskHarness() {
  const [open, setOpen] = useState(true)
  return <>
    <button onClick={() => setOpen(true)}>Open test drawer</button>
    <AskDrawer open={open} onClose={() => setOpen(false)} />
  </>
}

export function RescueHarness() {
  const [open, setOpen] = useState(true)
  const [recalled, setRecalled] = useState<number[]>([])
  return <>
    <button onClick={() => setOpen(true)}>Open test rescue</button>
    <output data-testid="recalled">{recalled.join(',')}</output>
    <RejectedRescueModal open={open} onClose={() => setOpen(false)} onRecalled={id => setRecalled(prev => [...prev, id])} />
  </>
}
