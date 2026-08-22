import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth-context'

const modules = [
  {
    key: 'precoi',
    title: 'Pre-COI',
    description: 'Create and update COI Master workbooks from GO, YPD, MES, PPO and CM sources.',
    action: 'Open Pre-COI',
    icon: '↗',
    path: '/pre-coi',
    tone: 'precoi',
  },
  {
    key: 'process',
    title: 'COI Process',
    description: 'Search GO data, review COI workspace, refresh and issue the current production COI.',
    action: 'Open COI Process',
    icon: '⌁',
    path: '/coi-process',
    tone: 'process',
  },
]

export default function COIHome() {
  const navigate = useNavigate()
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
        {visibleModules.map((module) => (
          <article className={`module-card ${module.tone}`} key={module.key}>
            <div className="module-icon" aria-hidden="true">{module.icon}</div>
            <div>
              <h2>{module.title}</h2>
              <p>{module.description}</p>
            </div>
            <button className="btn btn-primary module-action" onClick={() => navigate(module.path)}>
              {module.action}
            </button>
          </article>
        ))}
      </div>
    </section>
  )
}
