import { useState, useEffect, useCallback, memo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fetchCoiSheet, saveCoiEdits, refreshPpo, applyPpoRefresh, issueCoi, exportCoiExcel } from '../api'
import { useAuth } from '../auth-context'

function headerGroup(column) {
  const key = String(column?.key || column?.letter || '').toUpperCase()
  if (key.includes('QTY') || key.includes("Q'TY") || key.includes('ALLOCATE') || key.includes('SHORTAGE') || key.includes('%')) return 'metrics'
  if (key.includes('COLOR')) return 'color'
  if (key.includes('JOB') || key.includes('LOT') || key.includes('SIZE') || key.includes('DATE')) return 'order'
  return 'identity'
}

const SheetRow = memo(function SheetRow({ row, columns, editableFields, canEdit, edits, onCellEdit }) {
  const editableFieldSet = editableFields
  return (
    <tr>
      {columns.map((col) => {
        const colKey = col.key || col.letter
        const editable = canEdit && editableFieldSet.has(colKey)
        const editKey = `${row._row_key}|${colKey}`
        const displayValue = edits[editKey]?.value ?? row[colKey] ?? ''
        return (
          <td
            key={colKey}
            className={editable ? 'editable' : ''}
            contentEditable={editable}
            suppressContentEditableWarning
            onBlur={(e) => {
              if (editable) onCellEdit(row._row_key, colKey, e.target.textContent, row._storage, row[colKey])
            }}
            style={colKey === 'PPO' ? { color: 'var(--primary)', cursor: 'pointer', fontWeight: 600 } : {}}
          >
            {displayValue}
          </td>
        )
      })}
    </tr>
  )
})

export default function COIWorkspace() {
  const { canEdit } = useAuth()
  const [searchParams] = useSearchParams()
  const go = searchParams.get('go') || ''

  const [sheetData, setSheetData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [toast, setToast] = useState(null)
  const [edits, setEdits] = useState({})
  const [refreshPreview, setRefreshPreview] = useState(null)
  const [applyingPreview, setApplyingPreview] = useState(false)

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

  const handleCellEdit = useCallback((rowKey, colKey, value, storage, originalValue) => {
    setEdits((prev) => {
      const key = `${rowKey}|${colKey}`
      if (String(value ?? '') === String(originalValue ?? '')) {
        const next = { ...prev }
        delete next[key]
        return next
      }
      return { ...prev, [key]: { row_key: rowKey, storage: storage || {}, field: colKey, value } }
    })
  }, [])

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
      const result = await refreshPpo(go)
      if (result?.preview_required) {
        setRefreshPreview(result)
      } else {
        showToast('PPO refreshed', 'success')
        loadSheet()
      }
    } catch (e) {
      showToast(e.message, 'error')
    }
  }

  const handleApplyRefresh = async () => {
    if (!refreshPreview?.preview_id) return
    setApplyingPreview(true)
    try {
      const result = await applyPpoRefresh(go, refreshPreview.preview_id)
      setRefreshPreview(null)
      showToast(result?.message || 'PPO refresh applied', 'success')
      loadSheet()
    } catch (e) {
      showToast(e.message, 'error')
    } finally {
      setApplyingPreview(false)
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
        <button className="btn coi-action-button action-refresh" onClick={loadSheet}>1 Refresh</button>
        {canEdit && <button className="btn coi-action-button action-ppo" onClick={handleRefreshPpo}>2 Refresh PPO</button>}
        {canEdit && <button className="btn coi-action-button action-export" onClick={handleExportExcel}>3 Export Excel</button>}
        {canEdit && <button className="btn coi-action-button action-issue" onClick={handleIssueCoi}>4 ISSUE COI</button>}
        {!canEdit && <span className="read-only-note">Read-only access</span>}
        {canEdit && Object.keys(edits).length > 0 && (
          <button className="btn coi-action-button action-save" onClick={handleSaveEdits}>
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
                <th className={`sheet-header-${headerGroup(col)}`} key={col.key || col.letter}>{col.label || col.key || col.letter}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <SheetRow
                key={row._row_key || ri}
                row={row}
                columns={columns}
                editableFields={editableFields}
                canEdit={canEdit}
                edits={edits}
                onCellEdit={handleCellEdit}
              />
            ))}
          </tbody>
        </table>
      </div>

      {toast && (
        <div className="toast-container">
          <div className={`toast ${toast.type}`}>{toast.msg}</div>
        </div>
      )}

      {refreshPreview && (
        <div className="coi-preview-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setRefreshPreview(null)}>
          <section className="coi-preview-modal" role="dialog" aria-modal="true" aria-labelledby="coi-preview-title">
            <div>
              <p className="eyebrow">SOURCE REFRESH PREVIEW</p>
              <h2 id="coi-preview-title">Apply PPO refresh for {go}?</h2>
              <p>PostgreSQL has not been changed. Review the summary before applying.</p>
            </div>
            <div className="coi-preview-summary">
              <span><strong>{refreshPreview.diff?.added_row_count || 0}</strong> rows added</span>
              <span><strong>{refreshPreview.diff?.removed_row_count || 0}</strong> rows removed</span>
              <span><strong>{refreshPreview.diff?.changed_row_count || 0}</strong> rows changed</span>
              <span><strong>{refreshPreview.diff?.change_count || 0}</strong> field changes</span>
            </div>
            <p className="coi-preview-note">AH Allocate and User Remark values are preserved. If this GO changes before Apply, the request will be rejected and you must preview again.</p>
            <div className="coi-preview-actions">
              <button className="btn" onClick={() => setRefreshPreview(null)} disabled={applyingPreview}>Cancel</button>
              <button className="btn btn-primary" onClick={handleApplyRefresh} disabled={applyingPreview}>
                {applyingPreview ? 'Applying...' : 'Apply refresh'}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
