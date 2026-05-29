import { useParams, useNavigate } from 'react-router-dom'
import { useJob, useDeleteJob } from '../api/jobs'
import { useRanking, useFunnel } from '../api/results'
import { Spinner } from '../components/ui/LoadingStates'
import { PageError } from '../components/ui/ErrorStates'
import { LogPanel } from '../components/jobs/LogPanel'
import { FunnelVisualization } from '../components/jobs/FunnelVisualization'
import { RankingTable } from '../components/jobs/RankingTable'

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b', running: '#3b82f6', completed: '#10b981', failed: '#ef4444', paused: '#6b7280',
}

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const { data: job, isLoading, isError, error, refetch } = useJob(jobId!)
  const deleteJob = useDeleteJob()
  const ranking = useRanking(jobId!, !!job && job.status === 'completed')
  const funnel = useFunnel(jobId!, !!job && job.status === 'completed')

  if (isLoading) return <Spinner />
  if (isError) return <PageError message={error?.message ?? 'Failed to load job'} onRetry={refetch} />
  if (!job) return null

  const color = STATUS_COLORS[job.status] ?? '#6b7280'

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 16px' }}>
      <button onClick={() => navigate('/jobs')} style={{ marginBottom: 16, cursor: 'pointer' }}>← Back to Jobs</button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>{job.name ?? job.id.slice(0, 8)}</h1>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span style={{ padding: '4px 14px', borderRadius: 14, background: color + '22', color, fontWeight: 600 }}>
            {job.status.toUpperCase()}
          </span>
          {(job.status === 'pending' || job.status === 'running') && (
            <button
              onClick={() => deleteJob.mutate(job.id, { onSuccess: () => navigate('/jobs') })}
              disabled={deleteJob.isPending}
              style={{ padding: '4px 14px', cursor: 'pointer', background: '#fef2f2', color: '#ef4444', border: '1px solid #fca5a5', borderRadius: 6 }}
            >
              Pause
            </button>
          )}
        </div>
      </div>

      {job.current_round && (
        <p style={{ color: '#6b7280' }}>
          Current round: <strong>{job.current_round}</strong>
          {job.processed_count > 0 && ` — ${job.processed_count.toLocaleString()} processed`}
        </p>
      )}

      {job.error_message && (
        <div style={{ padding: 12, background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, color: '#991b1b', marginBottom: 20 }}>
          {job.error_message}
        </div>
      )}

      <section style={{ marginTop: 24 }}>
        <h2>Rounds</h2>
        <FunnelVisualization
          rounds={(funnel.data?.rounds ?? []).filter(r => r.total_items !== null && r.total_items > 0)}
        />
        <LogPanel jobId={job.id} active={job.status === 'running' || job.status === 'pending'} />
      </section>

      <section>
        <h2>Results</h2>
        {job.status !== 'completed' ? (
          <div style={{ background: '#f9fafb', borderRadius: 8, padding: 16, color: '#9ca3af', textAlign: 'center' }}>
            Results will appear when the job completes.
          </div>
        ) : ranking.isLoading ? (
          <Spinner />
        ) : ranking.isError ? (
          <div style={{ background: '#fef2f2', borderRadius: 8, padding: 16, color: '#991b1b', textAlign: 'center' }}>
            Failed to load ranking data.
          </div>
        ) : ranking.data && ranking.data.items.length > 0 ? (
          <RankingTable items={ranking.data.items} />
        ) : (
          <div style={{ background: '#f9fafb', borderRadius: 8, padding: 16, color: '#9ca3af', textAlign: 'center' }}>
            No ranking results available.
          </div>
        )}
      </section>
    </div>
  )
}
