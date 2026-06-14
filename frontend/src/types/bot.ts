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
  agent?: string
}

export interface Position {
  symbol: string
  ticket?: number
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

export interface AgentPerformance {
  samples: number
  win_rate: number
  sl_hit_rate: number
  tp_hit_rate: number
  avg_move_pct: number
}

export interface AgentParams {
  min_confidence: number
  min_rr: number
  atr_sl_mult: number
  atr_tp_mult: number
  lot_size: number
}

export interface AgentInfo {
  name: string
  symbol: string
  description: string
  provider: string
  model: string
  params: AgentParams
  stats: { signals: number; trades: number; holds: number }
  performance: AgentPerformance
}

export interface OptimizationEntry {
  agent: string
  symbol: string
  performance: AgentPerformance
  hold_rate: number
  reasons: string[]
  changes: string[]
  applied: boolean
}

export interface AgentsOverview {
  agents: AgentInfo[]
  optimize_every_cycles: number
  last_optimization: OptimizationEntry[] | null
  last_optimization_at: string | null
}

export interface CoordinatorSymbolState {
  exposure_notional: number
  exposure_pct: number
  gross_exposure_pct: number
  net_exposure_pct: number
  net_volume: number
  net_direction: 'LONG' | 'SHORT' | 'FLAT'
  long_positions: number
  short_positions: number
  floating_pnl: number
  open_positions: number
  max_allocation_pct: number
  remaining_pct: number
}

export interface CoordinatorSnapshot {
  equity: number
  balance: number
  free_margin: number
  used_margin: number
  total_exposure_pct: number
  max_total_exposure_pct: number
  max_symbol_allocation_pct: number
  max_net_direction_pct: number
  reversal_drawdown_pct: number
  max_symbol_loss_pct: number
  hedging: boolean
  daily_pnl_pct: number | null
  in_cooldown: boolean
  open_positions_total: number
  can_close: boolean
  symbols: Record<string, CoordinatorSymbolState>
}

export interface CoordinatorDecision {
  symbol: string
  approve: boolean
  priority: number
  allocation_pct: number
  position_action: 'hold' | 'reduce' | 'close' | 'hedge'
  manage_direction?: 'BUY' | 'SELL'
  reason: string
  clamp?: string
}

export interface Coordination {
  snapshot: CoordinatorSnapshot
  rationale: string
  decisions: CoordinatorDecision[]
}

export interface CoordinatorOverview {
  enabled: boolean
  provider?: string
  model?: string
  can_close?: boolean
  max_total_exposure_pct?: number
  max_symbol_allocation_pct?: number
  max_net_direction_pct?: number
  reversal_drawdown_pct?: number
  max_symbol_loss_pct?: number
  last_coordination?: Coordination | null
  last_coordination_at?: string | null
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
