import { useCallback, useEffect, useMemo, useState } from 'react'
import { login as loginRequest, logout as logoutRequest, fetchCurrentUser } from './api'
import { AuthContext } from './auth-context'

const USER_KEY = 'coi-auth-user'

function readStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(readStoredUser)
  const [loading, setLoading] = useState(true)

  const clearAuth = useCallback(() => {
    localStorage.removeItem('coi-auth-token')
    localStorage.removeItem(USER_KEY)
    setUser(null)
  }, [])

  useEffect(() => {
    const token = localStorage.getItem('coi-auth-token')
    if (!token) {
      clearAuth()
      setLoading(false)
      return
    }
    fetchCurrentUser()
      .then((result) => {
        const nextUser = result.user
        localStorage.setItem(USER_KEY, JSON.stringify(nextUser))
        setUser(nextUser)
      })
      .catch(clearAuth)
      .finally(() => setLoading(false))
  }, [clearAuth])

  useEffect(() => {
    window.addEventListener('coi-auth-expired', clearAuth)
    return () => window.removeEventListener('coi-auth-expired', clearAuth)
  }, [clearAuth])

  const login = useCallback(async (username, password) => {
    const result = await loginRequest(username, password)
    localStorage.setItem('coi-auth-token', result.access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(result.user))
    setUser(result.user)
    return result.user
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutRequest()
    } finally {
      clearAuth()
    }
  }, [clearAuth])

  const value = useMemo(() => ({
    user,
    loading,
    isAuthenticated: Boolean(user),
    canEdit: user?.role === 'editor',
    login,
    logout,
  }), [user, loading, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
