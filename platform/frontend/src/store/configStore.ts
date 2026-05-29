import { create } from 'zustand'
import type { FunctionType, TierLevel, StructureTool } from '../types/config'

const DEFAULT_WEIGHTS = {
  anoxpepred: 1.0, bepipred3: 1.0, sodope: 1.0,
  temstapro: 1.0, plddt: 1.0, sasa: 1.0, agrescan3d: 1.0,
}

const DEFAULT_THRESHOLDS = {
  toxinpred3: 0.38, hemopi2: 0.55, mhcflurry: 0.5,
}

interface ConfigState {
  tier: TierLevel
  function_type: FunctionType | null
  job_name: string
  top_n: number
  linkers: string[]
  structure_tool: StructureTool
  weights: Record<string, number>
  thresholds: Record<string, number>

  setTier: (tier: TierLevel) => void
  setFunctionType: (ft: FunctionType) => void
  setJobName: (name: string) => void
  setTopN: (n: number) => void
  setLinkers: (linkers: string[]) => void
  setStructureTool: (tool: StructureTool) => void
  setWeight: (key: string, value: number) => void
  setThreshold: (key: string, value: number) => void

  toJobConfig: () => Record<string, unknown> | null  // returns null if invalid
}

export const useConfigStore = create<ConfigState>()((set, get) => ({
  tier: 'A',
  function_type: null,
  job_name: '',
  top_n: 10000,
  linkers: ['Flex_GGGGSx2'],
  structure_tool: 'omegafold',
  weights: { ...DEFAULT_WEIGHTS },
  thresholds: { ...DEFAULT_THRESHOLDS },

  setTier: (tier) => set({ tier }),
  setFunctionType: (function_type) => set({ function_type }),
  setJobName: (job_name) => set({ job_name }),
  setTopN: (top_n) => set({ top_n }),
  setLinkers: (linkers) => set({ linkers }),
  setStructureTool: (structure_tool) => set({ structure_tool }),
  setWeight: (key, value) => set(s => ({ weights: { ...s.weights, [key]: value } })),
  setThreshold: (key, value) => set(s => ({ thresholds: { ...s.thresholds, [key]: value } })),

  toJobConfig: () => {
    const { tier, function_type, top_n, linkers, structure_tool, weights, thresholds } = get()
    if (!function_type) return null

    const config: Record<string, unknown> = { function_type, tier }
    if (tier === 'B' || tier === 'C') {
      if (top_n < 1000 || top_n > 100000) return null
      if (linkers.length === 0) return null
      config.top_n = top_n
      config.linkers = linkers
      config.structure_tool = structure_tool
    }
    if (tier === 'C') {
      config.weights = weights
      config.thresholds = thresholds
    }
    return config
  },
}))
