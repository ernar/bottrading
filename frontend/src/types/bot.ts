export interface Signal {
  symbol: string
  trend: 'bullish' | 'bearish' | 'sideways'
  action: 'buy' | 'sell' | 'hold'
  confidence: number
  entry: number
  stop_loss: number
  take_profit: number
  risk_level: 'low' | 'medium' | 'high'
  reason: string
  timestamp: string
  platform?: string
}

export interface Position {
  symbol: string
  direction: 'BUY' | 'SELL'
  volume: number
  open_price: number
  current_price: number
  profit: number
  stop_loss?: number
  take_profit?: number
}

export interface Trade {
  symbol: string
  action: string
  entry_price: number
  exit_price: number | null
  volume: number
  pnl: number
  open_time: string
  close_time: string | null
  duration_seconds: number | null
}

export interface AccountInfo {
  balance: number
  equity: number
  free_margin: number
  used_margin: number
  margin_level: number
  leverage: number
  platform?: string
}

export interface BotState {
  signals: Record<string, Signal>
  positions: Record<string, Position>
  closed_trades: Trade[]
  account_info: AccountInfo | null
  bot_running: boolean
  connected: boolean
  last_update: string
}
