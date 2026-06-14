import { useState } from 'react'
import { BotState } from '../types/bot'
import { StatusBadge } from './StatusBadge'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface HeaderProps {
  state: BotState | null
  connected: boolean
}

function BotToggleButton({ running }: { running: boolean }) {
  const [busy, setBusy] = useState(false)

  const toggle = async () => {
    setBusy(true)
    try {
      await fetch(`${API_URL}/api/bot/${running ? 'stop' : 'start'}`, {
        method: 'POST',
        headers: getApiHeaders(),
      })
    } catch { /* el estado real llega por WebSocket */ }
    setBusy(false)
  }

  return (
    <button
      onClick={toggle}
      disabled={busy}
      className={`px-4 py-1.5 rounded font-semibold text-sm transition disabled:opacity-50 ${
        running
          ? 'bg-red-600 hover:bg-red-700 text-white'
          : 'bg-green-600 hover:bg-green-700 text-white'
      }`}
    >
      {busy ? '...' : running ? '■ Detener' : '▶ Iniciar'}
    </button>
  )
}

export function Header({ state, connected }: HeaderProps) {
  if (!state || !state.account_info) {
    return (
      <header className="bg-gray-900 text-white p-4 border-b border-gray-700">
        <div className="flex justify-between items-center gap-2">
          <h1 className="text-xl sm:text-2xl font-bold">Trading Bot</h1>
          <StatusBadge status={connected ? 'connected' : 'disconnected'} label="API" />
        </div>
      </header>
    )
  }

  const account = state.account_info
  const openPositions = Object.keys(state.positions).length
  const floatingPnl = account.equity - account.balance
  const pnlColor = floatingPnl > 0 ? 'text-green-400' : floatingPnl < 0 ? 'text-red-400' : 'text-white'
  const platform = account.platform || 'MT4'

  return (
    <header className="bg-gray-900 text-white p-4 border-b border-gray-700">
      <div className="flex flex-wrap justify-between items-center gap-y-2 mb-4">
        <div className="flex items-center gap-3">
          <h1 className="text-xl sm:text-2xl font-bold">{platform} Trading Bot</h1>
          {state.last_update && (
            <span className="text-xs text-gray-500 hidden sm:inline">
              actualizado {new Date(state.last_update).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 sm:gap-4">
          <StatusBadge status={connected ? 'connected' : 'disconnected'} label="API" />
          <StatusBadge status={state.bot_running ? 'bullish' : 'bearish'} label={state.bot_running ? 'Running' : 'Stopped'} />
          <BotToggleButton running={state.bot_running} />
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 sm:gap-4 text-sm">
        <div className="bg-gray-800 p-3 rounded">
          <div className="text-gray-400">Balance</div>
          <div className="text-lg sm:text-xl font-semibold">${account.balance.toFixed(2)}</div>
        </div>
        <div className="bg-gray-800 p-3 rounded">
          <div className="text-gray-400">Equity</div>
          <div className="text-lg sm:text-xl font-semibold">${account.equity.toFixed(2)}</div>
        </div>
        <div className="bg-gray-800 p-3 rounded">
          <div className="text-gray-400">P/L flotante</div>
          <div className={`text-lg sm:text-xl font-semibold ${pnlColor}`}>
            {floatingPnl >= 0 ? '+' : ''}${floatingPnl.toFixed(2)}
          </div>
        </div>
        <div className="bg-gray-800 p-3 rounded">
          <div className="text-gray-400">Free Margin</div>
          <div className="text-lg sm:text-xl font-semibold">${account.free_margin.toFixed(2)}</div>
        </div>
        <div className="bg-gray-800 p-3 rounded">
          <div className="text-gray-400">Posiciones</div>
          <div className="text-lg sm:text-xl font-semibold">{openPositions}</div>
        </div>
        <div className="bg-gray-800 p-3 rounded">
          <div className="text-gray-400">Leverage</div>
          <div className="text-lg sm:text-xl font-semibold">{account.leverage}:1</div>
        </div>
      </div>
    </header>
  )
}
