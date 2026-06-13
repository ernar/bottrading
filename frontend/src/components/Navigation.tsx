import { Link, useLocation } from 'react-router-dom'

const LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/signals', label: 'Signals' },
  { to: '/positions', label: 'Positions' },
  { to: '/history', label: 'History' },
]

export function Navigation() {
  const location = useLocation()

  const isActive = (path: string) =>
    location.pathname === path ? 'border-b-2 border-blue-500 text-white' : 'text-gray-300'

  return (
    <nav className="bg-gray-800 border-b border-gray-700">
      <div className="flex gap-6 px-6">
        {LINKS.map(({ to, label }) => (
          <Link
            key={to}
            to={to}
            className={`px-4 py-3 hover:bg-gray-700 hover:text-white transition ${isActive(to)}`}
          >
            {label}
          </Link>
        ))}
      </div>
    </nav>
  )
}
