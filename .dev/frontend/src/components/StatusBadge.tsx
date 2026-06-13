interface StatusBadgeProps {
  status: 'bullish' | 'bearish' | 'sideways' | 'connected' | 'disconnected'
  label?: string
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const colorMap = {
    bullish: 'bg-green-100 text-green-800',
    bearish: 'bg-red-100 text-red-800',
    sideways: 'bg-gray-100 text-gray-800',
    connected: 'bg-green-100 text-green-800',
    disconnected: 'bg-red-100 text-red-800'
  }

  const displayText = label || status.charAt(0).toUpperCase() + status.slice(1)

  return (
    <span className={`px-3 py-1 rounded-full text-sm font-semibold ${colorMap[status]}`}>
      {displayText}
    </span>
  )
}
