import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient, toApiError } from './client'
import type { Job } from '../types/config'
import { useAuthStore } from '../store/authStore'

export interface JobListResponse {
  jobs: Job[]
  total: number
  page: number
  page_size: number
}

export function useJobs(page = 1, pageSize = 20, statusFilter?: string) {
  return useQuery<JobListResponse, Error>({
    queryKey: ['jobs', page, pageSize, statusFilter],
    queryFn: async () => {
      const params: Record<string, string | number> = { page, page_size: pageSize }
      if (statusFilter) params.status = statusFilter
      const { data } = await apiClient.get<JobListResponse>('/jobs', { params })
      return data
    },
  })
}

export function useJob(jobId: string) {
  return useQuery<Job, Error>({
    queryKey: ['job', jobId],
    queryFn: async () => {
      const { data } = await apiClient.get<Job>(`/jobs/${jobId}`)
      return data
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'running' || status === 'pending' ? 5000 : false
    },
  })
}

export function useCreateJob() {
  const qc = useQueryClient()
  return useMutation<Job, Error, { config: Record<string, unknown>; name?: string }>({
    mutationFn: async (req) => {
      const { data } = await apiClient.post<Job>('/jobs', req)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
    onError: toApiError,
  })
}

export function useDeleteJob() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (jobId) => {
      await apiClient.delete(`/jobs/${jobId}`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

export interface JobEventItem {
  id: string
  round: string | null
  event: string | null
  message: string | null
  timestamp: string
}

export function useJobEvents(jobId: string, active: boolean) {
  const [events, setEvents] = useState<JobEventItem[]>([])
  const [connected, setConnected] = useState(false)
  const lastEventIdRef = useRef<string | null>(null)

  useEffect(() => {
    if (!active) return

    let cancelled = false
    let currentAbort: AbortController | null = null

    async function poll() {
      while (!cancelled) {
        const token = useAuthStore.getState().token
        currentAbort = new AbortController()
        try {
          const params = new URLSearchParams()
          if (lastEventIdRef.current) params.set('last_event_id', lastEventIdRef.current)

          const res = await fetch(`/api/jobs/${jobId}/events?${params}`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: currentAbort.signal,
          })

          if (!res.body) break
          setConnected(true)

          const reader = res.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''

          while (!cancelled) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })

            const lines = buffer.split('\n')
            buffer = lines.pop() ?? ''

            let eventType = ''
            let dataLine = ''
            for (const line of lines) {
              if (line.startsWith('event: ')) eventType = line.slice(7).trim()
              else if (line.startsWith('data: ')) dataLine = line.slice(6).trim()
              else if (line === '' && dataLine) {
                if (eventType === 'done') { cancelled = true; break }
                if (eventType === 'job_event') {
                  const parsed = JSON.parse(dataLine) as JobEventItem
                  setEvents(prev => [...prev, parsed])
                  lastEventIdRef.current = parsed.id
                }
                eventType = ''
                dataLine = ''
              }
            }
          }
          setConnected(false)
        } catch (err) {
          if ((err as { name?: string }).name === 'AbortError') break
          setConnected(false)
          if (!cancelled) await new Promise<void>(r => setTimeout(r, 3000))
        }
      }
    }

    poll()

    return () => {
      cancelled = true
      currentAbort?.abort()
      setConnected(false)
    }
  }, [jobId, active])

  return { events, connected }
}
