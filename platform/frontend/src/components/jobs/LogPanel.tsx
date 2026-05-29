import { useEffect, useMemo, useRef, useState } from 'react'
import { useJobEvents } from '../../api/jobs'

const EVENT_COLORS: Record<string, string> = {
  round_started: '#3b82f6',
  round_completed: '#10b981',
  round_failed: '#ef4444',
  job_started: '#8b5cf6',
  job_completed: '#10b981',
  job_failed: '#ef4444',
  job_paused: '#6b7280',
}

export function LogPanel({ jobId, active }: { jobId: string; active: boolean }) {
  const { events, connected } = useJobEvents(jobId, active)
  const [roundFilter, setRoundFilter] = useState<string>('all')
  const scrollRef = useRef<HTMLDivElement>(null)

  const rounds = ['all', ...Array.from(new Set(events.map(e => e.round).filter((r): r is string => r !== null)))]
  const filtered = useMemo(
    () => roundFilter === 'all' ? events : events.filter(e => e.round === roundFilter),
    [events, roundFilter]
  )

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [filtered])

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>
          Live Log {connected ? '🟢' : '⚫'}
        </span>
        <select
          value={roundFilter}
          onChange={e => setRoundFilter(e.target.value)}
          style={{ fontSize: 12, border: '1px solid #d1d5db', borderRadius: 4, padding: '2px 6px' }}
        >
          {rounds.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      <div
        ref={scrollRef}
        style={{ height: 300, overflowY: 'auto', padding: 12, fontFamily: 'monospace', fontSize: 12, background: '#1a1a1a', color: '#d1d5db' }}
      >
        {filtered.length === 0 ? (
          <span style={{ color: '#6b7280' }}>Waiting for events...</span>
        ) : (
          filtered.map(e => (
            <div key={e.id} style={{ marginBottom: 4 }}>
              <span style={{ color: '#6b7280' }}>{e.timestamp.slice(11, 19)} </span>
              {e.round && <span style={{ color: '#60a5fa', marginRight: 6 }}>[{e.round}]</span>}
              {e.event && <span style={{ color: EVENT_COLORS[e.event] ?? '#fbbf24', marginRight: 6 }}>{e.event}</span>}
              <span>{e.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
