// Auth-стор: единственная точка доступа к токенам (v1 — localStorage,
// httpOnly-cookie в будущем). Логин/refresh идут сырым fetch, fetchMe — через
// api-клиент, чтобы работал авто-refresh при 401 (цикл импортов безопасен:
// оба модуля обращаются друг к другу только во время вызовов).
import { defineStore } from 'pinia'
import { get } from '../api/client'
import type { TokenPair, User } from '../api/types'

const ACCESS_KEY = 'erp.access_token'
const REFRESH_KEY = 'erp.refresh_token'

async function jsonFetch<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return (await response.json()) as T
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    accessToken: localStorage.getItem(ACCESS_KEY) ?? '',
    refreshToken: localStorage.getItem(REFRESH_KEY) ?? '',
    user: null as User | null,
  }),
  getters: {
    isAuthenticated: (state) => !!state.accessToken,
    userName: (state) => state.user?.full_name || state.user?.email || '',
  },
  actions: {
    persist(tokens: TokenPair) {
      this.accessToken = tokens.access_token
      this.refreshToken = tokens.refresh_token
      localStorage.setItem(ACCESS_KEY, tokens.access_token)
      localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
    },
    async login(email: string, password: string): Promise<void> {
      const tokens = await jsonFetch<TokenPair>('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      this.persist(tokens)
      await this.fetchMe()
    },
    async fetchMe(): Promise<void> {
      // через api-клиент: мусорный access-токен вызовет refresh и повтор,
      // а не разлогин (см. приёмку спеки фронтенда)
      this.user = await get<User>('/auth/me')
    },
    async refreshTokens(): Promise<boolean> {
      if (!this.refreshToken) return false
      try {
        const tokens = await jsonFetch<TokenPair>('/api/v1/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
        })
        this.persist(tokens)
        return true
      } catch {
        return false
      }
    },
    logout() {
      this.accessToken = ''
      this.refreshToken = ''
      this.user = null
      localStorage.removeItem(ACCESS_KEY)
      localStorage.removeItem(REFRESH_KEY)
    },
    forceLogout() {
      this.logout()
      window.location.assign('/login')
    },
  },
})
