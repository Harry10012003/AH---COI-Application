import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchCoiSheet, saveCoiEdits, refreshPpo, issueCoi, exportCoiExcel } from '../api'
import { useAuth } from '../auth-context'

export default function COIWorkspace() {
  const { canEdit } = useAuth()
  const [searchParams] = useSearchParams()
  const go = searchParams.get('go') || ''

  const [sheetData, setSheetData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [toast, setToast] = useState(null)
  const [edits, setEdits] = useState({})

  const loadSheet = useCallback(async () => {
    if (!go) return
    setLoading(true)
    setError('')
    try {
      const data = await fetchCoiSheet(go)
      setSheetData(data)
      setEdits({})
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [go])

  useEffect(() => { loadSheet() }, [loadSheet])

  const showToast = (msg, type = '') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const handleCellEdit = (rowKey, colKey, value) => {
    setEdits((prev) => {
      const key = `${rowKey}|${colKey}`
      if (value === '' || value === null) {
        const next = { ...prev }
        delete next[key]
        return next
      }
      return { ...prev, [key]: { row_key: rowKey, field: colKey, value } }
    })
  }

  const handleSaveEdits = async () => {
    const editList = Object.values(edits)
    if (!editList.length) return
    try {
      await saveCoiEdits(go, editList)
      showToast('Saved', 'success')
      setEdits({})
      loadSheet()
    } catch (e) {
      showToast(e.message, 'error')
    }
  }

  const handleRefreshPpo = async () => {
    try {
      await refreshPpo(go)
      showToast('PPO refreshed', 'success')
      loadSheet()
    } catch (e) {
      showToast(e.message, 'error')
    }
  }

  const handleIssueCoi = async () => {
    try {
      const result = await issueCoi(go)
      showToast(result?.message || 'COI Issued', 'success')
    } catch (e) {
      showToast(e.message, 'error')
    }
  }

  const handleExportExcel = async () => {
    try {
      await exportCoiExcel(go)
      showToast('Exported', 'success')
    } catch (e) {
      showToast(e.message, 'error')
    }
  }

  if (!go) {
    return <div className="empty-state"><h2>No GO selected</h2><p>Go back to Home and select a GO.</p></div>
  }

  if (loading) {
    return <div className="loading-screen"><div className="spinner" /><span>Loading COI data...</span></div>
  }

  if (error) {
    return <div className="empty-state"><h2>Error</h2><p>{error}</p><button className="btn btn-primary" onClick={loadSheet} style={{ marginTop: 12 }}>Retry</button></div>
  }

  const columns = sheetData?.sheet?.columns || sheetData?.columns || []
  const rows = sheetData?.sheet?.rows || sheetData?.rows || []
  const cacheProfile = sheetData?.cache_profile || {}
  const emptyReason = cacheProfile.reason || (sheetData?.pending ? 'COI data is being prepared. Please refresh in a moment.' : '')
  const editableFields = new Set(['PPO', 'AH Allocate Q\'ty (yds)', 'User Remark'])

  return (
    <div className="flex flex-col" style={{ height: '100%' }}>
      <div className="coi-toolbar">
        <span className="go-title">GO #{go}</span>
        <button className="btn btn-primary" onClick={loadSheet}>Refresh</button>
        {canEdit && <button className="btn btn-primary" onClick={handleRefreshPpo}>Refresh PPO</button>}
        {canEdit && <button className="btn btn-primary" onClick={handleExportExcel}>Export Excel</button>}
        {canEdit && <button className="btn btn-primary" onClick={handleIssueCoi}>ISSUE COI</button>}
        {!canEdit && <span className="read-only-note">Read-only access</span>}
        {canEdit && Object.keys(edits).length > 0 && (
          <button className="btn btn-primary" onClick={handleSaveEdits}>
            Save ({Object.keys(edits).length})
          </button>
        )}
        <div className="flex-1" />
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          {rows.length} rows
        </span>
      </div>

      <div className="sheet-grid-wrap">
        {rows.length === 0 && emptyReason && (
          <div className="empty-state" style={{ minHeight: 180 }}>
            <h2>{cacheProfile.state === 'WAIT_PPO' ? 'PPO / fabric data is not available yet' : 'COI data is not ready'}</h2>
            <p>{emptyReason}</p>
          </div>
        )}
        <table className="sheet-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col.key || col.letter}>{col.label || col.key || col.letter}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={row._row_key || ri}>
                {columns.map((col) => {
                  const colKey = col.key || col.letter
                  const editable = canEdit && editableFields.has(colKey)
                  const editKey = `${row._row_key}|${colKey}`
                  const displayValue = edits[editKey]?.value ?? row[colKey] ?? ''
                  return (
                    <td
                      key={colKey}
                      className={editable ? 'editable' : ''}
                      contentEditable={editable}
                      suppressContentEditableWarning
                      onBlur={(e) => {
                        if (editable) handleCellEdit(row._row_key, colKey, e.target.textContent)
                      }}
                      style={colKey === 'PPO' ? { color: 'var(--primary)', cursor: 'pointer', fontWeight: 600 } : {}}
                    >
                      {displayValue}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {toast && (
        <div className="toast-container">
          <div className={`toast ${toast.type}`}>{toast.msg}</div>
        </div>
      )}
    </div>
  )
}
