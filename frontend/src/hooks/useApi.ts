import axios from 'axios'
import { BotState, Signal, Position, Trade, AccountInfo } from '../types/bot'
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

  return {
    getSignals,
    getPositions,
    getAccount,
    getHistory,
    getState,
    startBot,
    stopBot,
    closePosition
  }
}
