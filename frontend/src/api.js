function authHeaders(headers = {}) {
  const token = localStorage.getItem('coi-auth-token')
  return { ...headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) }
}

async function parseError(res) {
  const payload = await res.json().catch(() => null)
  return payload?.error || payload?.detail || `HTTP ${res.status}`
}

async function request(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: authHeaders({ 'Content-Type': 'application/json', ...options.headers }),
  })
  if (!res.ok) {
    if (res.status === 401 && !url.endsWith('/auth/login')) {
      window.dispatchEvent(new Event('coi-auth-expired'))
    }
    throw new Error(await parseError(res))
  }
  return res.json()
}

export function login(username, password) {
  return request('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
}

export function fetchCurrentUser() {
  return request('/api/auth/me')
}

export function logout() {
  return request('/api/auth/logout', { method: 'POST' })
}

export async function fetchGoList({ search, factories, coiReady } = {}) {
  const params = new URLSearchParams()
  if (search) params.set('search', search)
  if (factories) params.set('factories', factories)
  if (coiReady && coiReady !== 'all') params.set('coi_ready', coiReady)
  return request(`/api/sql/go/list?${params}`)
}

export function fetchCoiSheet(go) {
  return request(`/api/sql/go/${encodeURIComponent(go)}/sheet`)
}

export function saveCoiEdits(go, edits) {
  return request(`/api/sql/go/${encodeURIComponent(go)}/sheet/edits`, {
    method: 'POST',
    body: JSON.stringify({ edits }),
  })
}

export function refreshPpo(go) {
  return request(`/api/sql/go/${encodeURIComponent(go)}/refresh-ppo`, { method: 'POST' })
}

export function issueCoi(go) {
  return request(`/api/sql/go/${encodeURIComponent(go)}/issue`, {
    method: 'POST',
    body: JSON.stringify({ go }),
  })
}

export async function exportCoiExcel(go) {
  const res = await fetch(`/api/sql/go/${encodeURIComponent(go)}/sheet/export`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ go }),
  })
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event('coi-auth-expired'))
    throw new Error(await parseError(res))
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `${go}-COI.xlsx`
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(href)
}

async function precoiRequest(url, formData) {
  const res = await fetch(url, { method: 'POST', headers: authHeaders(), body: formData })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export function startPreCoiJob(action, { goText, ypdUsername, ypdPassword, workbook }) {
  const form = new FormData()
  if (goText) form.set('go_text', goText)
  if (ypdUsername) form.set('ypd_username', ypdUsername)
  if (ypdPassword) form.set('ypd_password', ypdPassword)
  if (workbook) form.set('workbook', workbook, workbook.name)
  const endpoint = { create: 'create', cm: 'cm', 'update-ppo': 'update-ppo', 'update-yy': 'update-yy' }[action]
  if (!endpoint) throw new Error('Unknown Pre-COI action')
  return precoiRequest(`/api/precoi/jobs/${endpoint}`, form)
}

export function fetchPreCoiJob(jobId) {
  return request(`/api/precoi/jobs/${encodeURIComponent(jobId)}`)
}

export function fetchPreCoiDraft(jobId) {
  return request(`/api/precoi/jobs/${encodeURIComponent(jobId)}/draft`)
}

export function savePreCoiDraft(jobId, revision, edits) {
  return request(`/api/precoi/jobs/${encodeURIComponent(jobId)}/draft`, {
    method: 'POST',
    body: JSON.stringify({ revision, edits }),
  })
}

export function startPreCoiDraftUpdate(jobId, action, { ypdUsername, ypdPassword } = {}) {
  const endpoint = action === 'update-ppo' ? 'update-ppo' : action === 'update-yy' ? 'update-yy' : ''
  if (!endpoint) throw new Error('Unknown Pre-COI draft action')
  return request(`/api/precoi/jobs/${encodeURIComponent(jobId)}/${endpoint}`, {
    method: 'POST',
    body: endpoint === 'update-yy' ? JSON.stringify({ ypd_username: ypdUsername, ypd_password: ypdPassword }) : JSON.stringify({}),
  })
}

async function fetchPreCoiJobDownload(jobId) {
  const res = await fetch(`/api/precoi/jobs/${encodeURIComponent(jobId)}/download`, { headers: authHeaders() })
  if (!res.ok) throw new Error(await parseError(res))
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || 'COI Master.xlsx'
  return { blob, filename }
}

function downloadBlob(blob, filename) {
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(href)
}

export async function savePreCoiJob(jobId, suggestedFilename = 'Pre-COI Output.xlsx') {
  let saveHandle = null
  if (window.showSaveFilePicker) {
    try {
      saveHandle = await window.showSaveFilePicker({
        suggestedName: suggestedFilename,
        types: [{ description: 'Excel Workbook', accept: { 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] } }],
      })
    } catch (error) {
      if (error?.name === 'AbortError') return { cancelled: true }
      throw new Error('Cannot open the Save As dialog.')
    }
  }

  const { blob, filename } = await fetchPreCoiJobDownload(jobId)
  if (saveHandle) {
    const writable = await saveHandle.createWritable()
    await writable.write(blob)
    await writable.close()
    return { filename, savedToFolder: true }
  }

  downloadBlob(blob, filename)
  return { filename, savedToFolder: false }
}

export async function downloadPreCoiJob(jobId) {
  const { blob, filename } = await fetchPreCoiJobDownload(jobId)
  downloadBlob(blob, filename)
  return { filename, savedToFolder: false }
}
