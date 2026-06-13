import axios from 'axios'
import { BotState, Signal, Position, Trade, AccountInfo } from '../types/bot'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'
const API_TOKEN = import.meta.env.VITE_API_TOKEN || ''

const api = axios.create({
  baseURL: API_URL,
  timeout: 10000,
  // Si el backend tiene API_TOKEN configurado, las rutas que mutan estado
  // (start/stop, cerrar posición...) exigen esta cabecera.
  headers: API_TOKEN ? { 'X-API-Token': API_TOKEN } : {},
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
