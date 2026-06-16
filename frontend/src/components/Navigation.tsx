import { Link, useLocation } from 'react-router-dom'

const LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/charts', label: 'Gráficos' },
  { to: '/coordinator', label: 'Mesa' },
  { to: '/agents', label: 'Agents' },
  { to: '/positions', label: 'Positions' },
  { to: '/history', label: 'History' },
  { to: '/settings', label: 'Ajustes' },
]

export function Navigation() {
  const location = useLocation()

  const isActive = (path: string) =>
    location.pathname === path ? 'border-b-2 border-blue-500 text-white' : 'text-gray-300'

  return (
    <nav className="bg-gray-800 border-b border-gray-700">
      {/* En móvil los enlaces no caben: scroll horizontal sin saltos de línea. */}
      <div className="flex gap-1 sm:gap-4 px-2 sm:px-6 overflow-x-auto whitespace-nowrap">
        {LINKS.map(({ to, label }) => (
          <Link
            key={to}
            to={to}
            className={`px-3 sm:px-4 py-3 text-sm sm:text-base shrink-0 hover:bg-gray-700 hover:text-white transition ${isActive(to)}`}
          >
            {label}
          </Link>
        ))}
      </div>
    </nav>
  )
}
