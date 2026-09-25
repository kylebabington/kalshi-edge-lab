import { NavLink, Outlet } from 'react-router-dom'
import { isMockMode } from '../api/client'

const nav = [
  { to: '/', label: 'Dashboard' },
  { to: '/markets', label: 'Markets' },
  { to: '/weather-model', label: 'Weather Model' },
  { to: '/research', label: 'Research' },
]

type Props = {
  apiStatus: string
  modelStatus: string
}

export function AppShell({ apiStatus, modelStatus }: Props) {
  return (
    <div className="min-h-screen grid grid-cols-1 lg:grid-cols-[240px_1fr]">
      <aside className="border-b lg:border-b-0 lg:border-r border-[var(--border)] bg-[color-mix(in_oklab,var(--surface)_88%,black)] px-4 py-5 flex flex-col gap-6">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-muted)]">Edge Lab</div>
          <div className="mt-1 text-xl font-semibold">Kalshi Edge Lab</div>
          <div className="mt-2">
            <span className="badge badge-warning">Research Only</span>
            {isMockMode() ? <span className="badge badge-info ml-2">Mock Data</span> : null}
          </div>
        </div>
        <nav className="flex flex-row lg:flex-col gap-2">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm transition ${
                  isActive
                    ? 'bg-[var(--surface-raised)] text-[var(--text)] border border-[var(--border)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-2 text-xs text-[var(--text-muted)]">
          <div className="status-chip">API: {apiStatus}</div>
          <div className="status-chip">Model: {modelStatus}</div>
          <div className="status-chip">Mode: RESEARCH ONLY</div>
        </div>
      </aside>
      <div className="min-w-0">
        <header className="border-b border-[var(--border)] px-5 py-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Kalshi Edge Lab</h1>
            <p className="text-sm text-[var(--text-muted)]">External Evidence Engine</p>
          </div>
          <span className="badge badge-warning">Research Only · No Bet</span>
        </header>
        <main className="px-5 py-5">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
