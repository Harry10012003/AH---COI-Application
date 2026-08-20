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
