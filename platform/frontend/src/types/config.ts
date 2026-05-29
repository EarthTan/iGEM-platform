export type FunctionType = 'antioxidant' | 'antimicrobial' | 'antiglycation'
export type TierLevel = 'A' | 'B' | 'C'
export type StructureTool = 'omegafold' | 'esmfold'
export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused'

export interface WeightsConfig {
  anoxpepred?: number
  bepipred3?: number
  sodope?: number
  temstapro?: number
  plddt?: number
  sasa?: number
  agrescan3d?: number
}

export interface ThresholdsConfig {
  plddt_min?: number
  agrescan3d_max?: number
  sasa_max?: number
  anoxpepred_min?: number
}

export interface RoundSplits {
  round1_top_pct?: number
  round1_bottom_pct?: number
}

export interface JobConfig {
  function_type: FunctionType
  tier: TierLevel
  top_n: number
  linkers: string[]
  structure_tool: StructureTool
  weights?: WeightsConfig
  thresholds?: ThresholdsConfig
  round_splits?: RoundSplits
}

export interface Job {
  id: string
  user_id: string
  config_id: string | null
  name: string | null
  status: JobStatus
  current_round: string | null
  processed_count: number
  total_count: number | null
  output_path: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
}

export interface User {
  id: string
  email: string
  created_at: string
}
