import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchGoList } from '../api'

export default function GOSelector() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [goList, setGoList] = useState([])
  const [total, setTotal] = useState(0)
  const [coiReadyFilter, setCoiReadyFilter] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const requestSequence = useRef(0)

  useEffect(() => {
    const trimmed = search.trim()
    if (trimmed.length > 0 && trimmed.length < 2) {
      setLoading(false)
      return undefined
    }
    const sequence = ++requestSequence.current
    const timer = setTimeout(async () => {
      setLoading(true)
      setError('')
      try {
        const data = await fetchGoList({ search, coiReady: coiReadyFilter })
        if (sequence !== requestSequence.current) return
        setGoList(data?.rows || [])
        setTotal(data?.total || 0)
      } catch (e) {
        if (sequence === requestSequence.current) setError(e.message)
      } finally {
        if (sequence === requestSequence.current) setLoading(false)
      }
    }, trimmed ? 250 : 0)
    return () => clearTimeout(timer)
  }, [search, coiReadyFilter])

  const handleSearch = (val) => setSearch(val)
  const handleCoiReadyFilter = (value) => setCoiReadyFilter(value)

  const handleSelect = (go) => {
    navigate(`/coi?go=${go.go_no}`)
  }

  const handleQuickGo = (e) => {
    e.preventDefault()
    const val = search.trim().toUpperCase()
    if (val) navigate(`/coi?go=${val}`)
  }

  const coiStatusLabel = (go) => {
    if (go.coi_status === 'AVAILABLE') return `COI Available (${go.snapshot_row_count || 0})`
    if (go.coi_status === 'BLOCKED') return 'COI Blocked'
    return 'COI Waiting Source'
  }

  const coiStatusClass = (go) => {
    if (go.coi_status === 'AVAILABLE') return 'badge-success'
    if (go.coi_status === 'BLOCKED') return 'badge-danger'
    return 'badge-warning'
  }

  return (
    <div className="go-selector">
      <div className="card go-selector-card">
        <h1>COI Workspace</h1>
        <p>Select a GO or enter a GO number to start working. {total > 0 && `${total} GO available`}</p>

        <form onSubmit={handleQuickGo} className="flex gap-8 mb-12">
          <input
            className="input flex-1"
            placeholder="Search by GO#, Style, or Status..."
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            autoFocus
          />
          <button type="submit" className="btn btn-primary">Go</button>
        </form>
        <div className="flex gap-8 mb-12 items-center">
          <label htmlFor="coi-ready-filter" className="filter-label">COI data</label>
          <select
            id="coi-ready-filter"
            className="input go-ready-filter"
            value={coiReadyFilter}
            onChange={(e) => handleCoiReadyFilter(e.target.value)}
          >
            <option value="all">All GO</option>
            <option value="ready">COI Available</option>
            <option value="not_ready">COI Waiting / Blocked</option>
          </select>
        </div>

        {loading && (
          <div className="loading-screen">
            <div className="spinner" />
            <span>Loading GO list...</span>
          </div>
        )}

        {error && <div className="toast error">{error}</div>}

        {!loading && !error && goList.length === 0 && search && (
          <div className="empty-state">
            <p>No GO found matching "{search}". Try a different search or press Enter to go directly.</p>
          </div>
        )}

        {!loading && goList.length > 0 && (
          <div className="go-results">
            {goList.map((go) => (
              <div
                key={go.go_no}
                className="go-result-item"
                onClick={() => handleSelect(go)}
              >
                <div>
                  <div className="go-number">{go.go_no}</div>
                  <div className="go-info">{go.style_no || go.style_desc || '-'}</div>
                </div>
                <div className="go-result-labels">
                  <span className={`badge ${go.status === 'Ready' ? 'badge-success' : go.status === 'Issue' ? 'badge-warning' : 'badge-primary'}`}>
                    {go.status || 'New'}
                  </span>
                  <span
                    className={`badge ${coiStatusClass(go)}`}
                    title={go.cache_reason || go.source_reason_code || ''}
                  >
                    {coiStatusLabel(go)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
