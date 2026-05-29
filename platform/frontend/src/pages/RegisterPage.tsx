import { useState, FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useRegister } from '../api/auth'

export function RegisterPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const register = useRegister()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await register.mutateAsync({ email, password })
      navigate('/')
    } catch (err: unknown) {
      const e = err as { message?: string }
      setError(e?.message ?? 'Registration failed')
    }
  }

  return (
    <div style={{ maxWidth: 400, margin: '100px auto', padding: 24 }}>
      <h1>iGEM Silk Platform</h1>
      <h2>Register</h2>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      <form onSubmit={handleSubmit}>
        <div>
          <label>Email</label>
          <br />
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required style={{ width: '100%', marginBottom: 8 }} />
        </div>
        <div>
          <label>Password</label>
          <br />
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required style={{ width: '100%', marginBottom: 16 }} />
        </div>
        <button type="submit" disabled={register.isPending}>
          {register.isPending ? 'Registering...' : 'Register'}
        </button>
      </form>
      <p>Have an account? <Link to="/login">Login</Link></p>
    </div>
  )
}
