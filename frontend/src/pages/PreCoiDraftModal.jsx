import { useEffect, useMemo, useRef, useState } from 'react'

const mainColumns = [
  ['go', 'GO'], ['yy_req_no', 'YY Req No'], ['marker_yy', 'Marker YY'], ['ppo_yy', 'PPO YY'],
  ['gmt_color', 'Gmt Color'], ['fabric_part', 'Fabric Part'], ['color_code', 'Color'], ['color_desc', 'Color Description'],
  ['jo', 'Job Order'], ['qty', 'Qty'], ['ppo_no', 'PPO'], ['ppo_qty', "PPO Q'ty"],
]

const collarColumns = [
  ['go', 'GO'], ['yy_req_no', 'YY Req No'], ['marker_yy', 'Marker YY'], ['ppo_yy', 'PPO YY'],
  ['gmt_color', 'Gmt Color'], ['fabric_part', 'Fabric Part'], ['color_code', 'Color'], ['color_desc', 'Color Description'],
  ['size', 'Size'], ['qty', 'Qty'], ['ppo_no', 'PPO'], ['ppo_qty', "PPO Q'ty"],
]

const ROW_HEIGHT = 34
const VIRTUALIZATION_THRESHOLD = 1000
const VIRTUALIZATION_OVERSCAN = 12

function editableField(field) {
  return field === 'ppo_no' || field === 'yy_req_no'
}

export default function PreCoiDraftModal({ draft, mode, onSave, onClose, onDownload }) {
  const [sheets, setSheets] = useState(draft.sheets)
  const [activeTab, setActiveTab] = useState(draft.sheets[0]?.key || '')
  const [dirty, setDirty] = useState(new Map())
  const [activeCell, setActiveCell] = useState(null)
  const [fill, setFill] = useState(null)
  const [fillHistory, setFillHistory] = useState([])
  const [redoHistory, setRedoHistory] = useState([])
  const [focusRequest, setFocusRequest] = useState(null)
  const [gridScrollTop, setGridScrollTop] = useState(0)
  const [saving, setSaving] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const gridRef = useRef(null)
  const pointerRef = useRef(null)
  const scrollFrameRef = useRef(null)
  const originalValuesRef = useRef(new Map())
  const isEditable = mode === 'edit'
  const activeSheet = sheets.find((sheet) => sheet.key === activeTab) || sheets[0]
  const columns = activeSheet?.key === 'COI' ? mainColumns : collarColumns
  const useVirtualRows = activeSheet && activeSheet.rows.length > VIRTUALIZATION_THRESHOLD
  const virtualStart = useVirtualRows ? Math.max(0, Math.floor(gridScrollTop / ROW_HEIGHT) - VIRTUALIZATION_OVERSCAN) : 0
  const virtualEnd = useVirtualRows ? Math.min(activeSheet.rows.length, virtualStart + Math.ceil(520 / ROW_HEIGHT) + (VIRTUALIZATION_OVERSCAN * 2)) : activeSheet?.rows.length || 0
  const renderedRows = activeSheet?.rows.slice(virtualStart, virtualEnd) || []
  const fillColumnLabel = columns.find(([field]) => field === fill?.field)?.[1] || fill?.field
  const fillCount = fill ? Math.abs(fill.targetIndex - fill.sourceIndex) + 1 : 0
  const changes = useMemo(() => Array.from(dirty.values()), [dirty])

  useEffect(() => {
    const originalValues = new Map()
    draft.sheets.forEach((sheet) => sheet.rows.forEach((row) => {
      ;['ppo_no', 'yy_req_no'].forEach((field) => originalValues.set(`${row.row_id}:${field}`, row[field] || ''))
    }))
    originalValuesRef.current = originalValues
    setSheets(draft.sheets)
    setActiveTab(draft.sheets[0]?.key || '')
    setDirty(new Map())
    setActiveCell(null)
    setFillHistory([])
    setRedoHistory([])
    setFocusRequest(null)
    setGridScrollTop(0)
  }, [draft])

  useEffect(() => {
    setGridScrollTop(0)
    gridRef.current?.scrollTo({ top: 0 })
  }, [activeTab])

  useEffect(() => {
    if (!focusRequest || !activeSheet) return
    const grid = gridRef.current
    if (!grid) return
    if (useVirtualRows) {
      const requestedTop = Math.max(0, (focusRequest.rowIndex * ROW_HEIGHT) - (grid.clientHeight / 2) + (ROW_HEIGHT / 2))
      const targetTop = Math.min(requestedTop, Math.max(0, grid.scrollHeight - grid.clientHeight))
      if (Math.abs(grid.scrollTop - targetTop) > 1) {
        grid.scrollTop = targetTop
        return
      }
    }
    const input = grid.querySelector(`input[data-row-index="${focusRequest.rowIndex}"][data-field="${focusRequest.field}"]`)
    if (input) {
      input.focus()
      setFocusRequest(null)
    }
  }, [activeSheet, focusRequest, gridScrollTop, useVirtualRows])

  useEffect(() => {
    if (!fill) return undefined

    const updateTargetFromPointer = () => {
      const pointer = pointerRef.current
      if (!pointer) return
      const cell = document.elementFromPoint(pointer.clientX, pointer.clientY)?.closest('td[data-fill-field]')
      if (!cell || cell.dataset.fillField !== fill.field) return
      const targetIndex = Number(cell.dataset.rowIndex)
      if (Number.isInteger(targetIndex)) {
        setFill((current) => current && current.field === fill.field && current.targetIndex !== targetIndex
          ? { ...current, targetIndex }
          : current)
      }
    }

    const finishFill = () => {
      if (!fill || fill.targetIndex === fill.sourceIndex) {
        setFill(null)
        return
      }
      const start = Math.min(fill.sourceIndex, fill.targetIndex)
      const end = Math.max(fill.sourceIndex, fill.targetIndex)
      const targetRows = activeSheet.rows.slice(start, end + 1)
      applyFill(targetRows, fill.field, fill.value)
      setFill(null)
    }

    const trackPointer = (event) => {
      pointerRef.current = { clientX: event.clientX, clientY: event.clientY }
      updateTargetFromPointer()
    }

    const autoScroll = () => {
      const pointer = pointerRef.current
      const grid = gridRef.current
      if (pointer && grid) {
        const rect = grid.getBoundingClientRect()
        const edge = 56
        let delta = 0
        if (pointer.clientY > rect.bottom - edge) delta = Math.min(22, Math.max(4, (pointer.clientY - (rect.bottom - edge)) * 0.42))
        if (pointer.clientY < rect.top + edge) delta = -Math.min(22, Math.max(4, ((rect.top + edge) - pointer.clientY) * 0.42))
        if (delta) {
          const before = grid.scrollTop
          grid.scrollTop += delta
          if (grid.scrollTop !== before) updateTargetFromPointer()
        }
      }
      scrollFrameRef.current = window.requestAnimationFrame(autoScroll)
    }

    window.addEventListener('pointerup', finishFill)
    window.addEventListener('pointermove', trackPointer)
    scrollFrameRef.current = window.requestAnimationFrame(autoScroll)
    return () => {
      window.removeEventListener('pointerup', finishFill)
      window.removeEventListener('pointermove', trackPointer)
      if (scrollFrameRef.current) window.cancelAnimationFrame(scrollFrameRef.current)
    }
  }, [fill, activeSheet])

  const applyChanges = (items) => {
    const updatesByRow = new Map()
    items.forEach((item) => {
      const rowUpdates = updatesByRow.get(item.rowId) || {}
      rowUpdates[item.field] = item.value
      updatesByRow.set(item.rowId, rowUpdates)
    })
    setSheets((currentSheets) => currentSheets.map((sheet) => ({
      ...sheet,
      rows: sheet.rows.map((row) => {
        const updates = updatesByRow.get(row.row_id)
        return updates ? { ...row, ...updates } : row
      }),
    })))
    setDirty((currentDirty) => {
      const nextDirty = new Map(currentDirty)
      items.forEach((item) => {
        const key = `${item.rowId}:${item.field}`
        if ((item.value || '') === (originalValuesRef.current.get(key) || '')) nextDirty.delete(key)
        else nextDirty.set(key, { row_id: item.rowId, field: item.field, value: item.value })
      })
      return nextDirty
    })
  }

  const applyFill = (targetRows, field, value) => {
    const previousValues = targetRows.map((row) => ({ rowId: row.row_id, field, value: row[field] || '' }))
    const nextValues = targetRows.map((row) => ({ rowId: row.row_id, field, value }))
    applyChanges(nextValues)
    setFillHistory((currentHistory) => [...currentHistory.slice(-49), { field, count: targetRows.length, previousValues, nextValues }])
    setRedoHistory([])
  }

  const clearFillHistory = () => {
    setFillHistory([])
    setRedoHistory([])
  }

  const save = async (closeAfterSave = false) => {
    if (!changes.length) {
      if (closeAfterSave) onClose()
      return
    }
    setSaving(true)
    try {
      await onSave(draft.revision, changes)
      if (closeAfterSave) onClose()
    } finally {
      setSaving(false)
    }
  }

  const close = () => {
    if (!dirty.size || window.confirm('Discard unsaved PPO / YY changes?')) onClose()
  }

  const download = async () => {
    setDownloading(true)
    try {
      await onDownload()
    } finally {
      setDownloading(false)
    }
  }

  const pasteValues = (event, rowIndex, field) => {
    const values = event.clipboardData.getData('text').replace(/\r/g, '').split('\n')
    if (values.length < 2) return
    event.preventDefault()
    clearFillHistory()
    const updates = values
      .slice(0, activeSheet.rows.length - rowIndex)
      .map((value, index) => ({ rowId: activeSheet.rows[rowIndex + index].row_id, field, value: value.split('\t')[0] }))
    applyChanges(updates)
  }

  const beginFill = (event, rowIndex, field, value) => {
    event.preventDefault()
    event.stopPropagation()
    pointerRef.current = { clientX: event.clientX, clientY: event.clientY }
    setFill({ field, sourceIndex: rowIndex, targetIndex: rowIndex, value })
  }

  const fillToLastRow = (event, rowIndex, field, value) => {
    event.preventDefault()
    event.stopPropagation()
    applyFill(activeSheet.rows.slice(rowIndex), field, value)
  }

  const moveFocusToCell = (rowIndex, field) => {
    const targetRow = activeSheet.rows[rowIndex]
    if (!targetRow) return
    setActiveCell({ rowId: targetRow.row_id, field })
    setFocusRequest({ rowIndex, field })
  }

  const navigateByArrow = (event, rowIndex, field) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    const nextIndex = event.key === 'ArrowUp' ? rowIndex - 1 : rowIndex + 1
    if (nextIndex < 0 || nextIndex >= activeSheet.rows.length) return
    event.preventDefault()
    moveFocusToCell(nextIndex, field)
  }

  const undoLastFill = () => {
    const transaction = fillHistory.at(-1)
    if (!transaction) return
    applyChanges(transaction.previousValues)
    setFillHistory((currentHistory) => currentHistory.slice(0, -1))
    setRedoHistory((currentHistory) => [...currentHistory, transaction])
  }

  const redoLastFill = () => {
    const transaction = redoHistory.at(-1)
    if (!transaction) return
    applyChanges(transaction.nextValues)
    setRedoHistory((currentHistory) => currentHistory.slice(0, -1))
    setFillHistory((currentHistory) => [...currentHistory, transaction])
  }

  if (!activeSheet) return null

  return (
    <div className="precoi-modal-backdrop" role="presentation">
      <section className="precoi-draft-modal" role="dialog" aria-modal="true" aria-labelledby="precoi-draft-title">
        <header className="precoi-draft-header">
          <div>
            <p className="eyebrow">PRE-COI {isEditable ? 'DRAFT' : 'RESULT'}</p>
            <h2 id="precoi-draft-title">{isEditable ? 'Review & Input PPO' : 'Review Result'}</h2>
            <p>{isEditable ? 'Edit PPO or YY Req No, then save the draft before running an update.' : 'Review the filled values in both sheets before downloading the final workbook.'}</p>
          </div>
          <button className="btn" onClick={close} disabled={saving}>Close</button>
        </header>

        <div className="precoi-draft-summary">
          <span>{sheets.reduce((total, sheet) => total + sheet.rows.length, 0)} rows</span>
          {isEditable && <span>{changes.length ? `${changes.length} unsaved cell(s)` : 'All changes saved'}</span>}
          {isEditable && <span className="precoi-fill-hint">Dùng ↑ / ↓ để di chuyển cùng cột. Nhấp đúp ô vuông để fill tới dòng cuối.</span>}
          {fill && <span className="precoi-fill-status" role="status">Fill {fillColumnLabel} to {fillCount} row{fillCount === 1 ? '' : 's'} — release to apply</span>}
          {!isEditable && <span>Workbook ready for download</span>}
        </div>

        <div className="precoi-tabs" role="tablist" aria-label="COI sheets">
          {sheets.map((sheet) => <button key={sheet.key} className={sheet.key === activeSheet.key ? 'active' : ''} role="tab" aria-selected={sheet.key === activeSheet.key} onClick={() => setActiveTab(sheet.key)}>{sheet.label} <span>{sheet.rows.length}</span></button>)}
        </div>

        <div className="precoi-draft-grid" ref={gridRef} onScroll={(event) => useVirtualRows && setGridScrollTop(event.currentTarget.scrollTop)}>
          <table>
            <thead><tr>{columns.map(([field, label]) => <th key={field} className={editableField(field) ? 'editable-header' : ''}>{label}</th>)}</tr></thead>
            <tbody>
              {useVirtualRows && virtualStart > 0 && <tr className="precoi-virtual-spacer" aria-hidden="true"><td colSpan={columns.length} style={{ height: virtualStart * ROW_HEIGHT }} /></tr>}
              {renderedRows.map((row, renderedIndex) => {
                const rowIndex = virtualStart + renderedIndex
                return <tr key={row.row_id} className="precoi-grid-data-row">
                {columns.map(([field]) => {
                  const editable = isEditable && editableField(field)
                  const selected = activeCell?.rowId === row.row_id && activeCell?.field === field
                  const fillTarget = fill?.field === field && fill.targetIndex === rowIndex
                  return <td key={field} data-fill-field={editable ? field : undefined} data-row-index={editable ? rowIndex : undefined} className={`${editable ? 'editable-cell' : ''} ${selected ? 'selected-cell' : ''} ${fillTarget ? 'fill-target-cell' : ''}`} onPointerEnter={() => fill && fill.field === field && setFill((current) => current?.targetIndex === rowIndex ? current : { ...current, targetIndex: rowIndex })}>
                    {editable ? <div className="precoi-cell-editor"><input data-row-index={rowIndex} data-field={field} value={row[field] || ''} onFocus={() => setActiveCell({ rowId: row.row_id, field })} onKeyDown={(event) => navigateByArrow(event, rowIndex, field)} onChange={(event) => { clearFillHistory(); applyChanges([{ rowId: row.row_id, field, value: event.target.value }]) }} onPaste={(event) => pasteValues(event, rowIndex, field)} aria-label={`${field} for ${row.go} ${row.fabric_part}`} />
                      {selected && <button className="precoi-fill-handle" aria-label={`Fill ${field} down`} title="Double-click to fill to the last row; drag to fill a range" onPointerDown={(event) => beginFill(event, rowIndex, field, row[field] || '')} onDoubleClick={(event) => fillToLastRow(event, rowIndex, field, row[field] || '')} />}
                    </div> : <span>{row[field] || ''}</span>}
                  </td>
                })}
                </tr>
              })}
              {useVirtualRows && virtualEnd < activeSheet.rows.length && <tr className="precoi-virtual-spacer" aria-hidden="true"><td colSpan={columns.length} style={{ height: (activeSheet.rows.length - virtualEnd) * ROW_HEIGHT }} /></tr>}
            </tbody>
          </table>
        </div>

        <footer className="precoi-draft-footer">
          {isEditable ? <><button className="btn" title={fillHistory.length ? `Undo ${fillHistory.at(-1).count} filled cells` : 'No fill action to undo'} onClick={undoLastFill} disabled={!fillHistory.length || saving}>Undo fill{fillHistory.length ? ` (${fillHistory.length})` : ''}</button><button className="btn" title={redoHistory.length ? `Redo ${redoHistory.at(-1).count} filled cells` : 'No fill action to redo'} onClick={redoLastFill} disabled={!redoHistory.length || saving}>Redo fill{redoHistory.length ? ` (${redoHistory.length})` : ''}</button><button className="btn" onClick={close} disabled={saving}>Cancel</button><button className="btn btn-primary" onClick={() => save(false)} disabled={!changes.length || saving}>{saving ? 'Saving...' : 'Save Draft'}</button><button className="btn btn-primary" onClick={() => save(true)} disabled={saving}>{saving ? 'Saving...' : 'Save & Close'}</button></> : <button className="btn btn-primary" onClick={download} disabled={downloading}>{downloading ? 'Preparing download...' : 'OK & Download'}</button>}
        </footer>
      </section>
    </div>
  )
}
