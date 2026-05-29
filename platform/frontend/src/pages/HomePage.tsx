import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useConfigStore } from '../store/configStore'
import { useAuthStore } from '../store/authStore'
import { useCreateJob } from '../api/jobs'
import type { FunctionType, TierLevel, StructureTool } from '../types/config'

const FUNCTION_TYPES: { value: FunctionType; label: string }[] = [
  { value: 'antioxidant', label: 'Antioxidant' },
  { value: 'antimicrobial', label: 'Antimicrobial' },
  { value: 'antiglycation', label: 'Antiglycation' },
]

const LINKER_OPTIONS = [
  'Flex_GGGGSx1', 'Flex_GGGGSx2', 'Flex_GGGGSx3',
  'Rigid_EAAAKx1', 'Rigid_EAAAKx2', 'Helix_AEAAAKEAAAKA',
  'PAS_linker', 'Silk_like_GS', 'Gly_rich_GPG', 'Pro_rich_PPP',
]

const STRUCTURE_TOOLS: { value: StructureTool; label: string }[] = [
  { value: 'omegafold', label: 'OmegaFold (default)' },
  { value: 'esmfold', label: 'ESMFold' },
]

const TIERS: { value: TierLevel; label: string; description: string }[] = [
  { value: 'A', label: 'Tier A', description: 'Quick start — just choose a function type' },
  { value: 'B', label: 'Tier B', description: 'Standard — tune top-N, linkers, structure tool' },
  { value: 'C', label: 'Tier C', description: 'Expert — full control over weights & thresholds' },
]

export function HomePage() {
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  const logout = useAuthStore(s => s.logout)
  const createJob = useCreateJob()
  const [submitError, setSubmitError] = useState<string | null>(null)

  const {
    tier, function_type, job_name, top_n, linkers, structure_tool, weights, thresholds,
    setTier, setFunctionType, setJobName, setTopN, setLinkers, setStructureTool,
    setWeight, setThreshold, toJobConfig,
  } = useConfigStore()

  async function handleRun() {
    setSubmitError(null)
    const config = toJobConfig()
    if (!config) {
      setSubmitError('Please fill in all required fields.')
      return
    }
    try {
      const job = await createJob.mutateAsync({
        config,
        name: job_name.trim() || undefined,
      })
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      setSubmitError((err as Error).message ?? 'Failed to create job.')
    }
  }

  function toggleLinker(linker: string) {
    if (linkers.includes(linker)) {
      if (linkers.length > 1) setLinkers(linkers.filter(l => l !== linker))
    } else {
      setLinkers([...linkers, linker])
    }
  }

  const inputStyle = {
    width: '100%', padding: '8px 10px', border: '1px solid #d1d5db',
    borderRadius: 6, fontSize: 14, boxSizing: 'border-box' as const,
  }
  const labelStyle = { display: 'block', fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 4 }
  const sectionStyle = { marginBottom: 20 }

  return (
    <div style={{ minHeight: '100vh', background: '#f9fafb' }}>
      <header style={{ background: '#fff', borderBottom: '1px solid #e5e7eb', padding: '12px 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 700, fontSize: 18 }}>iGEM Silk Platform</span>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', fontSize: 14 }}>
          <Link to="/jobs" style={{ color: '#3b82f6', textDecoration: 'none' }}>My Jobs</Link>
          <span style={{ color: '#6b7280' }}>{user?.email}</span>
          <button onClick={logout} style={{ cursor: 'pointer', background: 'none', border: '1px solid #d1d5db', borderRadius: 6, padding: '4px 12px', fontSize: 13 }}>
            Logout
          </button>
        </div>
      </header>

      <main style={{ maxWidth: 640, margin: '40px auto', padding: '0 16px' }}>
        <h1 style={{ marginBottom: 4 }}>Configure Pipeline</h1>
        <p style={{ color: '#6b7280', marginBottom: 32 }}>Design silk fibroin fusion proteins with functional peptides.</p>

        {/* Tier selector */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
          {TIERS.map(t => (
            <button
              key={t.value}
              onClick={() => setTier(t.value)}
              style={{
                flex: 1, padding: '10px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, fontWeight: 600,
                border: tier === t.value ? '2px solid #3b82f6' : '2px solid #e5e7eb',
                background: tier === t.value ? '#eff6ff' : '#fff',
                color: tier === t.value ? '#1d4ed8' : '#374151',
              }}
            >
              {t.label}
              <div style={{ fontSize: 11, fontWeight: 400, marginTop: 2, color: tier === t.value ? '#3b82f6' : '#9ca3af' }}>
                {t.description}
              </div>
            </button>
          ))}
        </div>

        {/* Config form */}
        <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 24 }}>

          {/* Tier A: function type */}
          <div style={sectionStyle}>
            <label style={labelStyle}>Function Type *</label>
            <div style={{ display: 'flex', gap: 8 }}>
              {FUNCTION_TYPES.map(ft => (
                <button
                  key={ft.value}
                  onClick={() => setFunctionType(ft.value)}
                  style={{
                    flex: 1, padding: '8px 4px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                    border: function_type === ft.value ? '2px solid #3b82f6' : '2px solid #e5e7eb',
                    background: function_type === ft.value ? '#eff6ff' : '#fff',
                    color: function_type === ft.value ? '#1d4ed8' : '#374151',
                    fontWeight: function_type === ft.value ? 600 : 400,
                  }}
                >
                  {ft.label}
                </button>
              ))}
            </div>
          </div>

          {/* Tier B+ */}
          {(tier === 'B' || tier === 'C') && (
            <>
              <div style={sectionStyle}>
                <label style={labelStyle}>Top N candidates</label>
                <input
                  type="number"
                  value={top_n}
                  min={1000}
                  max={100000}
                  step={1000}
                  onChange={e => setTopN(Number(e.target.value))}
                  style={inputStyle}
                />
                <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 3 }}>Range: 1,000 – 100,000 (default: 10,000)</div>
              </div>

              <div style={sectionStyle}>
                <label style={labelStyle}>Linkers</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {LINKER_OPTIONS.map(l => (
                    <button
                      key={l}
                      onClick={() => toggleLinker(l)}
                      style={{
                        padding: '4px 10px', borderRadius: 14, cursor: 'pointer', fontSize: 12,
                        border: linkers.includes(l) ? '1px solid #3b82f6' : '1px solid #d1d5db',
                        background: linkers.includes(l) ? '#eff6ff' : '#fff',
                        color: linkers.includes(l) ? '#1d4ed8' : '#6b7280',
                      }}
                    >
                      {l}
                    </button>
                  ))}
                </div>
                <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 3 }}>Select one or more. Last selected cannot be deselected.</div>
              </div>

              <div style={sectionStyle}>
                <label style={labelStyle}>Structure Prediction Tool</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  {STRUCTURE_TOOLS.map(st => (
                    <button
                      key={st.value}
                      onClick={() => setStructureTool(st.value)}
                      style={{
                        flex: 1, padding: '8px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                        border: structure_tool === st.value ? '2px solid #3b82f6' : '2px solid #e5e7eb',
                        background: structure_tool === st.value ? '#eff6ff' : '#fff',
                        color: structure_tool === st.value ? '#1d4ed8' : '#374151',
                      }}
                    >
                      {st.label}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Tier C */}
          {tier === 'C' && (
            <>
              <div style={sectionStyle}>
                <label style={labelStyle}>Scoring Weights</label>
                {Object.entries(weights).map(([key, val]) => (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ width: 120, fontSize: 12, color: '#374151' }}>{key}</span>
                    <input
                      type="number"
                      value={val}
                      min={0}
                      max={10}
                      step={0.1}
                      onChange={e => setWeight(key, Number(e.target.value))}
                      style={{ ...inputStyle, width: 80 }}
                    />
                  </div>
                ))}
              </div>

              <div style={sectionStyle}>
                <label style={labelStyle}>Safety Thresholds</label>
                {Object.entries(thresholds).map(([key, val]) => (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ width: 120, fontSize: 12, color: '#374151' }}>{key}</span>
                    <input
                      type="number"
                      value={val}
                      min={0}
                      max={1}
                      step={0.01}
                      onChange={e => setThreshold(key, Number(e.target.value))}
                      style={{ ...inputStyle, width: 80 }}
                    />
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Job name (optional) */}
          <div style={sectionStyle}>
            <label style={labelStyle}>Job Name (optional)</label>
            <input
              type="text"
              value={job_name}
              placeholder="e.g. My Antioxidant Run v1"
              onChange={e => setJobName(e.target.value)}
              style={inputStyle}
            />
          </div>

          {/* Run button */}
          {submitError && (
            <div style={{ padding: '10px 14px', background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, color: '#991b1b', fontSize: 13, marginBottom: 12 }}>
              {submitError}
            </div>
          )}

          <button
            onClick={handleRun}
            disabled={createJob.isPending || !function_type}
            style={{
              width: '100%', padding: '12px', borderRadius: 8, cursor: createJob.isPending || !function_type ? 'not-allowed' : 'pointer',
              background: createJob.isPending || !function_type ? '#9ca3af' : '#3b82f6',
              color: '#fff', fontSize: 15, fontWeight: 700, border: 'none',
            }}
          >
            {createJob.isPending ? 'Starting…' : '▶ Run Pipeline'}
          </button>
        </div>
      </main>
    </div>
  )
}
