import type { FunnelRound } from '../../api/results'

const ROUND_LABELS: Record<string, string> = {
  round0: 'R0 Preprocess',
  round1: 'R1 Antioxidant Split',
  round2: 'R2 Safety Screen',
  round3: 'R3 Precompute',
  round3_deep: 'R3 Deep Scoring',
  round3_graphcpp: 'R3 GraphCPP',
  round4: 'R4 Enumerate',
  round4_bepipred: 'R4 BepiPred3',
  round5: 'R5 3D Structure',
  round6: 'R6 PDB Eval',
  round7: 'R7 Final',
}

export function FunnelVisualization({ rounds }: { rounds: FunnelRound[] }) {
  if (rounds.length === 0) return null

  const maxItems = Math.max(...rounds.map(r => r.total_items ?? 0), 1)

  return (
    <div style={{ marginBottom: 24 }}>
      <h3 style={{ margin: '0 0 12px' }}>Pipeline Funnel</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {rounds.map((r) => {
          const count = r.processed_items ?? r.total_items ?? 0
          const pct = Math.max((count / maxItems) * 100, count > 0 ? 2 : 0)
          const label = ROUND_LABELS[r.round] ?? r.round
          return (
            <div key={`${r.round}-${r.step}`} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 140, textAlign: 'right', fontSize: 12, color: '#6b7280', flexShrink: 0 }}>
                {label}
              </div>
              <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 4, height: 20, overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: r.status === 'completed' ? '#3b82f6' : r.status === 'failed' ? '#ef4444' : '#d1d5db',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
              <div style={{ width: 80, fontSize: 12, color: '#374151', flexShrink: 0 }}>
                {count > 0 ? count.toLocaleString() : '—'}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
