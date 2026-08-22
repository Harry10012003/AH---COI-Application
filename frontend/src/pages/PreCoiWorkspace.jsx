import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchPreCoiDraft, fetchPreCoiJob, savePreCoiDraft, savePreCoiJob, startPreCoiDraftUpdate, startPreCoiJob } from '../api'
import { useAuth } from '../auth-context'
import PreCoiDraftModal from './PreCoiDraftModal'
import PreCoiGuideModal from './PreCoiGuideModal'

const CREDENTIAL_STORAGE_KEY = 'coi-precoi-escm-credentials-v1'

const actionMeta = {
  create: { label: 'Create Output', help: 'Create a new COI Master workbook from GO / YPD / MES.' },
  'update-yy': { label: 'Update YY Req No', help: 'Refresh Marker YY from YPD using the saved Pre-COI draft.' },
  'update-ppo': { label: 'Update PPO Qty', help: 'Update PPO Qty from PPO values saved in the Pre-COI draft.' },
  cm: { label: 'Update CM', help: 'Create the CM workbook directly from the GO list.' },
}

function getRememberedCredentials() {
  try {
    const value = JSON.parse(window.localStorage.getItem(CREDENTIAL_STORAGE_KEY) || '{}')
    return { username: value.username || '', password: value.password || '', remember: Boolean(value.username && value.password) }
  } catch {
    return { username: '', password: '', remember: false }
  }
}

export default function PreCoiWorkspace() {
  const navigate = useNavigate()
  const { canEdit } = useAuth()
  const remembered = getRememberedCredentials()
  const [goText, setGoText] = useState('')
  const [ypdUsername, setYpdUsername] = useState(remembered.username)
  const [ypdPassword, setYpdPassword] = useState(remembered.password)
  const [rememberCredentials, setRememberCredentials] = useState(remembered.remember)
  const [job, setJob] = useState(null)
  const [logs, setLogs] = useState([])
  const [error, setError] = useState('')
  const [draft, setDraft] = useState(null)
  const [draftMode, setDraftMode] = useState('')
  const [draftJobId, setDraftJobId] = useState('')
  const [lastResultJobId, setLastResultJobId] = useState('')
  const [guideOpen, setGuideOpen] = useState(false)

  const isRunning = job?.state === 'RUNNING'

  useEffect(() => {
    try {
      if (rememberCredentials && ypdUsername.trim() && ypdPassword) {
        window.localStorage.setItem(CREDENTIAL_STORAGE_KEY, JSON.stringify({ username: ypdUsername.trim(), password: ypdPassword }))
      } else {
        window.localStorage.removeItem(CREDENTIAL_STORAGE_KEY)
      }
    } catch {
      // Browser storage may be unavailable in private or locked-down profiles.
    }
  }, [rememberCredentials, ypdUsername, ypdPassword])

  const validate = (action) => {
    if (!goText.trim() && (action === 'create' || action === 'cm')) return 'GO / Batch GO is required.'
    if (action === 'create' && (!ypdUsername.trim() || !ypdPassword)) return 'ESCM account and password are required.'
    return ''
  }

  const loadDraft = async (jobId, mode) => {
    const nextDraft = await fetchPreCoiDraft(jobId)
    setDraft(nextDraft)
    setDraftJobId(jobId)
    setDraftMode(mode)
  }

  const pollJob = async (jobId, completeMode) => {
    try {
      const status = await fetchPreCoiJob(jobId)
      setJob(status)
      setLogs(status.logs || [])
      if (status.state === 'RUNNING') {
        window.setTimeout(() => pollJob(jobId, completeMode), 1200)
        return
      }
      if (status.state === 'DONE') {
        setLastResultJobId(jobId)
        await loadDraft(jobId, completeMode)
        return
      }
      setError(status.error || 'Pre-COI processing failed.')
    } catch (pollError) {
      setError(pollError.message || 'Cannot read Pre-COI job status.')
      setJob({ state: 'ERROR' })
    }
  }

  const startAction = async (action) => {
    const validationError = validate(action)
    if (validationError) {
      setError(validationError)
      return
    }
    setError('')
    setLogs([])
    setJob({ state: 'RUNNING', action })
    try {
      const started = await startPreCoiJob(action, { goText, ypdUsername, ypdPassword })
      await pollJob(started.job_id, action === 'create' ? 'edit' : 'result')
    } catch (startError) {
      setError(startError.message || 'Cannot start Pre-COI processing.')
      setJob({ state: 'ERROR' })
    }
  }

  const saveDraft = async (revision, edits) => {
    try {
      const saved = await savePreCoiDraft(draftJobId, revision, edits)
      setDraft(saved)
      setLogs((currentLogs) => [...currentLogs, `Draft revision ${saved.revision} saved.`])
    } catch (saveError) {
      setError(saveError.message || 'Cannot save Pre-COI draft.')
      throw saveError
    }
  }

  const updateDraft = async (action) => {
    if (!draftJobId) {
      setError('Create and save a draft before running an update.')
      return
    }
    if (action === 'update-yy' && (!ypdUsername.trim() || !ypdPassword)) {
      setError('ESCM account and password are required for Update YY Req No.')
      return
    }
    setError('')
    setDraftMode('')
    setJob({ state: 'RUNNING', action })
    try {
      const started = await startPreCoiDraftUpdate(draftJobId, action, { ypdUsername, ypdPassword })
      await pollJob(started.job_id, 'result')
    } catch (updateError) {
      setError(updateError.message || 'Cannot update Pre-COI draft.')
      setJob({ state: 'ERROR' })
    }
  }

  const downloadResult = async () => {
    if (!lastResultJobId || !draft?.download_name) return
    setError('')
    try {
      const saved = await savePreCoiJob(lastResultJobId, draft.download_name)
      if (saved.cancelled) return
      setLogs((currentLogs) => [...currentLogs, `Downloaded ${saved.filename}.`])
      setDraftMode('')
    } catch (downloadError) {
      setError(downloadError.message || 'Cannot download Pre-COI workbook.')
    }
  }

  const progressState = job?.state?.toLowerCase() || 'ready'

  return (
    <section className="precoi-page" aria-labelledby="precoi-title">
      <div className="precoi-header">
        <div>
          <button className="back-link" onClick={() => navigate('/')}>← Back to COI menu</button>
          <h1 id="precoi-title">Update Pre-COI</h1>
          <p>Create or update Pre-COI from GO / YPD / MES.</p>
        </div>
        <button className="btn precoi-guide-trigger" onClick={() => setGuideOpen(true)}>Hướng Dẫn</button>
      </div>

      {!canEdit && <p className="read-only-note">Viewer access is read-only. Ask an Editor to run Pre-COI actions.</p>}
      {error && <div className="precoi-error" role="alert">{error}</div>}

      <div className="precoi-card">
        <label className="precoi-label" htmlFor="precoi-go">GO / Batch GO</label>
        <textarea id="precoi-go" className="input precoi-go-input" value={goText} onChange={(event) => setGoText(event.target.value)} placeholder="S26V00001, S26V00002" disabled={isRunning || !canEdit} />

        <div className="precoi-credentials">
          <label className="form-group" htmlFor="precoi-account"><span className="precoi-label">Account ESCM</span><input id="precoi-account" className="input" value={ypdUsername} onChange={(event) => setYpdUsername(event.target.value)} placeholder="DOMAIN\username" autoComplete="username" disabled={isRunning || !canEdit} /></label>
          <label className="form-group" htmlFor="precoi-password"><span className="precoi-label">Password ESCM</span><input id="precoi-password" className="input" type="password" value={ypdPassword} onChange={(event) => setYpdPassword(event.target.value)} autoComplete="current-password" disabled={isRunning || !canEdit} /></label>
        </div>
        <label className="precoi-remember"><input type="checkbox" checked={rememberCredentials} onChange={(event) => setRememberCredentials(event.target.checked)} disabled={isRunning || !canEdit} /><span>Remember account/password on this browser profile (only use on your personal Windows account).</span></label>
        <p className="download-note">PPO and YY are entered in the Review table after Create Output. Download Excel opens a Save As dialog so you can choose the destination folder.</p>
      </div>

      <div className="precoi-actions" aria-label="Pre-COI actions">
        <button className="btn precoi-action create" title={actionMeta.create.help} onClick={() => startAction('create')} disabled={isRunning || !canEdit}>Create Output</button>
        <button className="btn" onClick={() => draft && setDraftMode('edit')} disabled={!draft || isRunning}>Review & Input PPO</button>
        <button className="btn precoi-action update-yy" title={actionMeta['update-yy'].help} onClick={() => updateDraft('update-yy')} disabled={!draftJobId || isRunning || !canEdit}>Update YY Req No</button>
        <button className="btn precoi-action update-ppo" title={actionMeta['update-ppo'].help} onClick={() => updateDraft('update-ppo')} disabled={!draftJobId || isRunning || !canEdit}>Update PPO Qty</button>
        <button className="btn precoi-action cm" title={actionMeta.cm.help} onClick={() => startAction('cm')} disabled={isRunning || !canEdit}>Update CM</button>
        <button className="btn" onClick={downloadResult} disabled={!lastResultJobId || isRunning}>Download Excel</button>
        <button className="btn" onClick={() => setLogs([])} disabled={isRunning}>Clear Log</button>
      </div>

      <div className="precoi-log-card" aria-live="polite">
        <div className="precoi-log-heading"><h2>Process Log</h2><span className={`job-state ${progressState}`}>{job?.state || 'READY'}</span></div>
        <div className={`precoi-progress ${progressState}`}><span /></div>
        <pre>{logs.length ? logs.join('\n') : 'Ready.'}</pre>
      </div>
      {draft && draftMode && <PreCoiDraftModal draft={draft} mode={draftMode} onSave={saveDraft} onClose={() => setDraftMode('')} onDownload={downloadResult} />}
      {guideOpen && <PreCoiGuideModal onClose={() => setGuideOpen(false)} />}
    </section>
  )
}
