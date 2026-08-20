import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'

export default function Login() {
  const { isAuthenticated, login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (isAuthenticated) return <Navigate to="/" replace />

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')
    if (!username.trim() || !password) {
      setError('Please enter your username and password.')
      return
    }
    try {
      setSubmitting(true)
      await login(username.trim(), password)
    } catch (loginError) {
      setError(loginError.message || 'Login failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-shell">
        <div className="login-brand">
          <img src="/favicon.svg" alt="Tessellation" className="login-logo" />
          <h1>COI Application System</h1>
          <p>Fabric Control &amp; Order Information</p>
        </div>

        <div className="login-card">
          <h2>Sign in</h2>
          <p className="login-intro">Use your assigned stakeholder account.</p>
          {error && <div className="login-error" role="alert">{error}</div>}

          <form onSubmit={handleSubmit} className="login-form">
            <label htmlFor="username">Username</label>
            <input id="username" className="input" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Enter username" autoComplete="username" autoFocus disabled={submitting} />

            <label htmlFor="password">Password</label>
            <div className="password-field">
              <input id="password" className="input" type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter password" autoComplete="current-password" disabled={submitting} />
              <button type="button" className="password-toggle" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>

            <button type="submit" className="btn btn-primary login-submit" disabled={submitting}>
              {submitting ? <><span className="button-spinner" /> Signing in...</> : 'Sign in'}
            </button>
          </form>
        </div>

        <p className="login-footer">Internal Use Only · Version 1.0 · 2026</p>
      </div>
    </div>
  )
}
