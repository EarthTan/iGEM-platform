import type { Job } from '../../types/config'

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  running: '#3b82f6',
  completed: '#10b981',
  failed: '#ef4444',
  paused: '#6b7280',
}

interface JobStatusCardProps {
  job: Job
  onClick: () => void
}

export function JobStatusCard({ job, onClick }: JobStatusCardProps) {
  const color = STATUS_COLORS[job.status] ?? '#6b7280'
  return (
    <div
      onClick={onClick}
      style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: 16, marginBottom: 12, cursor: 'pointer' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 600 }}>{job.name ?? job.id.slice(0, 8)}</span>
        <span style={{ padding: '2px 10px', borderRadius: 12, background: color + '22', color, fontSize: 12, fontWeight: 600 }}>
          {job.status.toUpperCase()}
        </span>
      </div>
      {job.current_round && (
        <p style={{ color: '#6b7280', fontSize: 13, margin: '6px 0 0' }}>
          Round: {job.current_round}
          {job.processed_count > 0 && ` — ${job.processed_count.toLocaleString()} processed`}
        </p>
      )}
      <p style={{ color: '#9ca3af', fontSize: 12, margin: '4px 0 0' }}>
        {new Date(job.created_at).toLocaleString()}
      </p>
    </div>
  )
}
