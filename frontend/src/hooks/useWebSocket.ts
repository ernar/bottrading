import { useEffect, useState, useCallback, useRef } from 'react'
import { io, Socket } from 'socket.io-client'
import { BotState, Signal, Position, AccountInfo, Trade } from '../types/bot'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

export function useWebSocket() {
  const [state, setState] = useState<BotState | null>(null)
  const [connected, setConnected] = useState(false)
  const [duplicateInstance, setDuplicateInstance] = useState(false)
  const socketRef = useRef<Socket | null>(null)

  useEffect(() => {
    const socket = io(API_URL, {
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      reconnectionAttempts: Infinity,
      timeout: 20000,
    })

    socketRef.current = socket

    socket.on('connect', () => setConnected(true))
    socket.on('disconnect', () => setConnected(false))
    socket.on('connect_error', () => setConnected(false))

    socket.on('initial_state', (data: BotState) => setState(data))
    socket.on('state_update', (data: BotState) => setState(data))

    socket.on('signal_update', (signal: Signal) => {
      setState(prev => prev ? {
        ...prev,
        signals: { ...prev.signals, [signal.symbol]: signal },
      } : null)
    })

    socket.on('position_update', ({ symbol, position }: { symbol: string; position: Position }) => {
      setState(prev => prev ? {
        ...prev,
        positions: { ...prev.positions, [position.ticket ?? symbol]: position },
      } : null)
    })

    socket.on('position_closed', ({ symbol }: { symbol: string }) => {
      setState(prev => {
        if (!prev) return null
        const positions = Object.fromEntries(
          Object.entries(prev.positions).filter(([, p]) => p.symbol !== symbol)
        )
        return { ...prev, positions }
      })
    })

    socket.on('account_update', (account: AccountInfo) => {
      setState(prev => prev ? { ...prev, account_info: account } : null)
    })

    socket.on('trade_closed', (trade: Trade) => {
      setState(prev => prev ? {
        ...prev,
        closed_trades: [...prev.closed_trades, trade],
      } : null)
    })

    socket.on('bot_status', ({ running }: { running: boolean }) => {
      setState(prev => prev ? { ...prev, bot_running: running } : null)
    })

    socket.on('duplicate_instance', () => setDuplicateInstance(true))

    return () => { socket.disconnect() }
  }, [])

  const emit = useCallback((event: string, data?: unknown) => {
    socketRef.current?.emit(event, data)
  }, [])

  return { state, connected, emit, duplicateInstance, clearDuplicate: () => setDuplicateInstance(false) }
}
