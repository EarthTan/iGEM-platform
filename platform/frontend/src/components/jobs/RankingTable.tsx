import { Fragment, useState } from 'react'
import type { RankingItem } from '../../api/results'

const PAGE_SIZE = 25

type SortKey = keyof RankingItem
type SortDir = 'asc' | 'desc'

const CHANNEL_COLORS: Record<string, { bg: string; color: string }> = {
  top: { bg: '#dcfce7', color: '#166534' },
  bottom: { bg: '#fef3c7', color: '#92400e' },
}

export function RankingTable({ items }: { items: RankingItem[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('global_rank')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
    setPage(1)
  }

  function toggleExpand(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const sorted = [...items].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    const cmp = typeof av === 'number' && typeof bv === 'number'
      ? av - bv
      : String(av).localeCompare(String(bv))
    return sortDir === 'asc' ? cmp : -cmp
  })

  const totalPages = Math.ceil(sorted.length / PAGE_SIZE)
  const pageItems = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  function SortHeader({ label, k }: { label: string; k: SortKey }) {
    const active = sortKey === k
    return (
      <th
        onClick={() => toggleSort(k)}
        style={{ cursor: 'pointer', padding: '8px 12px', textAlign: 'left', userSelect: 'none', whiteSpace: 'nowrap', background: '#f9fafb', borderBottom: '2px solid #e5e7eb' }}
      >
        {label} {active ? (sortDir === 'asc' ? '↑' : '↓') : ''}
      </th>
    )
  }

  return (
    <div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr>
              <SortHeader label="Rank" k="global_rank" />
              <SortHeader label="Channel" k="channel" />
              <SortHeader label="ID" k="construct_id" />
              <SortHeader label="Position" k="position" />
              <SortHeader label="Linker" k="linker" />
              <SortHeader label="Score" k="round7_score" />
              <th style={{ padding: '8px 12px', background: '#f9fafb', borderBottom: '2px solid #e5e7eb' }} />
            </tr>
          </thead>
          <tbody>
            {pageItems.map((item) => {
              const isExp = expanded.has(item.construct_id)
              const ch = CHANNEL_COLORS[item.channel] ?? { bg: '#f3f4f6', color: '#374151' }
              return (
                <Fragment key={item.construct_id}>
                  <tr
                    key={item.construct_id}
                    style={{ borderBottom: '1px solid #f3f4f6', cursor: 'pointer' }}
                    onClick={() => toggleExpand(item.construct_id)}
                  >
                    <td style={{ padding: '8px 12px' }}>{item.global_rank}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, background: ch.bg, color: ch.color, fontWeight: 600 }}>
                        {item.channel}
                      </span>
                    </td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace', fontSize: 12 }}>{item.construct_id}</td>
                    <td style={{ padding: '8px 12px' }}>{item.position}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace', fontSize: 11 }}>{item.linker}</td>
                    <td style={{ padding: '8px 12px', fontWeight: 600, color: '#1d4ed8' }}>{item.round7_score.toFixed(4)}</td>
                    <td style={{ padding: '8px 12px', color: '#9ca3af' }}>{isExp ? '▲' : '▼'}</td>
                  </tr>
                  {isExp && (
                    <tr key={`${item.construct_id}-exp`} style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                      <td colSpan={7} style={{ padding: '8px 16px 12px' }}>
                        <div style={{ fontSize: 12, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 16px' }}>
                          <span style={{ color: '#6b7280' }}>Sequence:</span>
                          <span style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{item.peptide_seq}</span>
                          <span style={{ color: '#6b7280' }}>Database:</span>
                          <span>{item.source_database}</span>
                          <span style={{ color: '#6b7280' }}>Accession:</span>
                          <span style={{ fontFamily: 'monospace' }}>{item.source_accession}</span>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 12 }}>
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} style={{ padding: '4px 12px', cursor: page === 1 ? 'default' : 'pointer' }}>
            ←
          </button>
          <span style={{ padding: '4px 8px', fontSize: 13, color: '#6b7280' }}>
            {page} / {totalPages}
          </span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} style={{ padding: '4px 12px', cursor: page === totalPages ? 'default' : 'pointer' }}>
            →
          </button>
        </div>
      )}
    </div>
  )
}
