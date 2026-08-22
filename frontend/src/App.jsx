import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AuthProvider } from './AuthContext'
import { useAuth } from './auth-context'
import COIHome from './pages/COIHome'
import GOSelector from './pages/GOSelector'
import COIWorkspace from './pages/COIWorkspace'
import Login from './pages/Login'
import PreCoiWorkspace from './pages/PreCoiWorkspace'

function PageLoader() {
  return <div className="loading-screen full-page"><div className="spinner" /><span>Loading...</span></div>
}

function ProtectedRoute({ children }) {
  const { loading, isAuthenticated } = useAuth()
  if (loading) return <PageLoader />
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return children
}

function PreCoiRoute({ children }) {
  const { user } = useAuth()
  if (user?.username?.trim().toLowerCase() !== 'ah') return <Navigate to="/" replace />
  return children
}

function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, canEdit, logout } = useAuth()
  const params = new URLSearchParams(location.search)
  const currentGo = params.get('go') || ''
  const isActive = (path) => location.pathname === path ? 'active' : ''

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-layout">
      <header className="header">
        <div className="header-left">
          <img src="/logotes.svg" alt="" className="header-logo" />
          <span className="app-name">COI</span>
        </div>
        <nav className="header-nav">
          <Link to="/" className={isActive('/')}>Home</Link>
          <Link to="/coi-process" className={isActive('/coi-process')}>COI Process</Link>
          {currentGo && <Link to={`/coi?go=${currentGo}`} className={isActive('/coi')}>COI Workspace</Link>}
        </nav>
        <div className="header-actions">
          {currentGo && <span className="go-badge">GO #{currentGo}</span>}
          {currentGo && <button className="btn btn-sm" onClick={() => navigate('/')}>Change</button>}
          <span className={`role-badge ${canEdit ? 'editor' : 'viewer'}`}>
            {user?.username} · {canEdit ? 'Editor' : 'Viewer'}
          </span>
          <button className="btn btn-sm logout-btn" onClick={handleLogout}>Logout</button>
        </div>
      </header>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<COIHome />} />
          <Route path="/pre-coi" element={<PreCoiRoute><PreCoiWorkspace /></PreCoiRoute>} />
          <Route path="/coi-process" element={<GOSelector />} />
          <Route path="/coi" element={<COIWorkspace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/*" element={<ProtectedRoute><AppLayout /></ProtectedRoute>} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  )
}
