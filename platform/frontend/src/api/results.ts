import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'

export interface RankingItem {
  global_rank: number
  channel: string
  channel_rank: number
  construct_id: string
  position: string
  linker: string
  peptide_seq: string
  source_database: string
  source_accession: string
  round7_score: number
}

export interface FunnelRound {
  round: string
  step: string | null
  status: string
  total_items: number | null
  processed_items: number | null
  started_at: string | null
  completed_at: string | null
}

export function useRanking(jobId: string, enabled: boolean) {
  return useQuery<{ items: RankingItem[]; total: number }, Error>({
    queryKey: ['ranking', jobId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/results/${jobId}/ranking`)
      return data
    },
    enabled,
    staleTime: Infinity,  // results never change once job is completed
  })
}

export function useFunnel(jobId: string, enabled: boolean) {
  return useQuery<{ rounds: FunnelRound[] }, Error>({
    queryKey: ['funnel', jobId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/results/${jobId}/funnel`)
      return data
    },
    enabled,
    staleTime: Infinity,
  })
}
