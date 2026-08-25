import { Link } from 'react-router-dom'
import { useAuth } from '../auth-context'

const modules = [
  {
    key: 'precoi',
    title: 'Pre-COI',
    description: 'Create and update COI Master workbooks from GO, YPD, MES, PPO and CM sources.',
    action: 'Open Pre-COI',
    icon: 'document',
    path: '/pre-coi',
    tone: 'precoi',
  },
  {
    key: 'process',
    title: 'COI Process',
    description: 'Search GO data, review COI workspace, refresh and issue the current production COI.',
    action: 'Open COI Process',
    icon: 'workspace',
    path: '/coi-process',
    tone: 'process',
  },
]

function ModuleIcon({ type }) {
  if (type === 'document') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 3.75h6.6L18 8.15v12.1H7z" />
        <path d="M13.5 3.75v4.7H18M9.75 12h5.5M9.75 15.5h4" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M4 10h16M9.5 10v9M14.5 10v9" />
    </svg>
  )
}

export default function COIHome() {
  const { user } = useAuth()
  const isPreCoiUser = user?.username?.trim().toLowerCase() === 'ah'
  const visibleModules = modules.filter((module) => module.key !== 'precoi' || isPreCoiUser)

  return (
    <section className="coi-home" aria-labelledby="coi-home-title">
      <div className="coi-home-intro">
        <p className="eyebrow">COI APPLICATION SYSTEM</p>
        <h1 id="coi-home-title">COI</h1>
        <p>Select a workspace to start. Your access level remains the same across both modules.</p>
      </div>

      <div className="module-grid">
        {visibleModules.map((module, index) => (
          <Link
            to={module.path}
            className={`module-card ${module.tone}`}
            key={module.key}
            aria-label={`${module.action}: ${module.description}`}
          >
            <span className="module-sequence" aria-hidden="true">0{index + 1}</span>
            <div className="module-icon"><ModuleIcon type={module.icon} /></div>
            <div className="module-copy">
              <h2>{module.title}</h2>
              <p>{module.description}</p>
            </div>
            <span className="module-action">
              {module.action}
              <span className="module-action-arrow" aria-hidden="true">→</span>
            </span>
          </Link>
        ))}
      </div>
    </section>
  )
}
