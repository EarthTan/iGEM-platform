import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useJobs } from '../api/jobs'
import { JobStatusCard } from '../components/jobs/JobStatusCard'
import { SkeletonCard } from '../components/ui/LoadingStates'
import { EmptyState } from '../components/ui/EmptyStates'
import { InlineError } from '../components/ui/ErrorStates'

export function JobsPage() {
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const navigate = useNavigate()
  const { data, isLoading, isError, error, refetch } = useJobs(page, 20, statusFilter)

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '24px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h1 style={{ margin: 0 }}>Jobs</h1>
        <select value={statusFilter ?? ''} onChange={(e) => { setStatusFilter(e.target.value || undefined); setPage(1) }}>
          <option value="">All</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="paused">Paused</option>
        </select>
      </div>

      {isLoading && [1, 2, 3].map((i) => <SkeletonCard key={i} />)}

      {isError && <InlineError message={error?.message ?? 'Failed to load jobs'} onRetry={refetch} />}

      {!isLoading && !isError && data?.jobs.length === 0 && (
        <EmptyState
          title="No jobs yet"
          description="Run your first pipeline job from the home page."
          actionLabel="Go to Home"
          onAction={() => navigate('/')}
        />
      )}

      {data?.jobs.map((job) => (
        <JobStatusCard key={job.id} job={job} onClick={() => navigate(`/jobs/${job.id}`)} />
      ))}

      {data && data.total > 20 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 16 }}>
          <button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
          <span>Page {page} of {Math.ceil(data.total / 20)}</span>
          <button disabled={page * 20 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      )}
    </div>
  )
}
