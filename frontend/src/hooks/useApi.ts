import axios from 'axios'
import { BotState, Signal, Position, Trade, AccountInfo, CandlesResponse, DbSignal } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

const api = axios.create({
  baseURL: API_URL,
  timeout: 10000,
  // Token (si el backend lo exige) + skip de la advertencia de ngrok.
  headers: getApiHeaders(),
})

export function useApi() {
  const getSignals = async (): Promise<Record<string, Signal>> => {
    const { data } = await api.get('/api/signals')
    return data
  }

  const getPositions = async (): Promise<Record<string, Position>> => {
    const { data } = await api.get('/api/positions')
    return data
  }

  const getAccount = async (): Promise<AccountInfo> => {
    const { data } = await api.get('/api/account')
    return data
  }

  const getHistory = async (): Promise<Trade[]> => {
    const { data } = await api.get('/api/history')
    return data
  }

  const getState = async (): Promise<BotState> => {
    const { data } = await api.get('/api/state')
    return data
  }

  // Velas H1 del símbolo para el gráfico.
  const getCandles = async (symbol: string, bars = 150): Promise<CandlesResponse> => {
    const { data } = await api.get(`/api/candles/${symbol}?bars=${bars}`)
    return data
  }

  // Histórico de señales persistidas del símbolo (para los marcadores). Solo
  // accionables (nonhold): el bot registra una señal por rotación y la mayoría
  // son HOLD, que ahogarían a las BUY/SELL dentro del límite.
  const getSignalHistory = async (symbol: string, limit = 200): Promise<DbSignal[]> => {
    const { data } = await api.get(`/api/db/signals?symbol=${symbol}&limit=${limit}&platform=mt4&nonhold=1`)
    return data
  }

  const startBot = async (): Promise<any> => {
    const { data } = await api.post('/api/bot/start')
    return data
  }

  const stopBot = async (): Promise<any> => {
    const { data } = await api.post('/api/bot/stop')
    return data
  }

  const closePosition = async (symbol: string): Promise<any> => {
    const { data } = await api.post(`/api/positions/${symbol}/close`)
    return data
  }

  const closeAllPositions = async (): Promise<any> => {
    const { data } = await api.post('/api/positions/close-all')
    return data
  }

  return {
    getSignals,
    getPositions,
    getAccount,
    getHistory,
    getState,
    getCandles,
    getSignalHistory,
    startBot,
    stopBot,
    closePosition,
    closeAllPositions
  }
}
