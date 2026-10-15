import { useRef, useState, type KeyboardEvent } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { isMockMode } from '../api/client'

const primary = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/opportunities', label: 'Opportunities', end: false },
  { to: '/explore', label: 'Explore', end: false },
  { to: '/markets', label: 'Weather', end: false },
  { to: '/sports', label: 'Sports', end: false },
  { to: '/watchlist', label: 'Watchlist', end: false },
]

const research = [
  { to: '/weather-model', label: 'Weather Model' },
  { to: '/research', label: 'Research' },
]

type Props = {
  apiStatus: string
}

function linkClass(isActive: boolean): string {
  const base = 'rounded-lg px-2 py-2 lg:px-3 text-xs sm:text-sm min-h-[2.25rem] flex items-center justify-center lg:justify-start transition'
  if (isActive) return `${base} bg-[var(--surface-raised)] text-[var(--text)] border border-[var(--border)]`
  return `${base} border border-transparent text-[var(--text-muted)] hover:text-[var(--text)]`
}

export function AppShell({ apiStatus }: Props) {
  const [moreOpen, setMoreOpen] = useState(false)
  const moreButton = useRef<HTMLButtonElement>(null)
  // Leaving a page closes the mobile research menu so it does not stay open over the next page.
  const { pathname } = useLocation()
  const [menuPath, setMenuPath] = useState(pathname)
  if (menuPath !== pathname) {
    setMenuPath(pathname)
    setMoreOpen(false)
  }

  function closeOnEscape(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Escape' || !moreOpen) return
    event.preventDefault()
    setMoreOpen(false)
    moreButton.current?.focus()
  }

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr] overflow-x-clip">
      <aside className="border-b lg:border-b-0 lg:border-r border-[var(--border)] bg-[color-mix(in_oklab,var(--surface)_88%,black)] px-4 py-4 lg:py-5 flex flex-col gap-4 lg:gap-6 max-w-full">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-muted)]">Edge Lab</div>
          <h1 className="mt-1 text-xl font-semibold">Kalshi Edge Lab</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <span className="badge badge-warning">Research Only</span>
            {isMockMode() ? <span className="badge badge-info">Mock Data</span> : null}
          </div>
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Research software. Does not place bets or give betting recommendations.
          </p>
        </div>
        <nav aria-label="Main" className="grid grid-cols-3 sm:grid-cols-6 gap-1 lg:flex lg:flex-col lg:gap-2">
          {primary.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => linkClass(isActive)}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div onKeyDown={closeOnEscape}>
          <button
            ref={moreButton}
            type="button"
            className="lg:hidden pager-button w-full text-sm"
            aria-expanded={moreOpen}
            aria-controls="research-tools"
            onClick={() => setMoreOpen((open) => !open)}
          >
            More
          </button>
          <div id="research-tools" className={moreOpen ? 'mt-3 space-y-2 lg:mt-0' : 'hidden lg:mt-0 lg:block space-y-2'}>
            <div className="text-xs uppercase tracking-[0.14em] text-[var(--text-muted)] pt-2 lg:pt-0 lg:border-t lg:border-[var(--border)]">
              Research tools
            </div>
            <nav aria-label="Research tools" className="flex flex-col gap-1">
              {research.map((item) => (
                <NavLink key={item.to} to={item.to} className={({ isActive }) => linkClass(isActive)}>
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
        </div>
        <div className="lg:mt-auto text-xs text-[var(--text-muted)]">
          <div className="status-chip">API: {apiStatus}</div>
        </div>
      </aside>
      <div className="min-w-0 max-w-full">
        <main className="px-4 sm:px-5 py-5 max-w-full">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
