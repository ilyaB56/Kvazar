// Auth-стор: единственная точка доступа к токенам (v1 — localStorage,
// httpOnly-cookie в будущем). Логин/refresh идут сырым fetch, fetchMe — через
// api-клиент, чтобы работал авто-refresh при 401 (цикл импортов безопасен:
// оба модуля обращаются друг к другу только во время вызовов).
import { defineStore } from 'pinia'
import { get } from '../api/client'
import type { TokenPair, User } from '../api/types'

const ACCESS_KEY = 'erp.access_token'
const REFRESH_KEY = 'erp.refresh_token'

// Ошибка HTTP с кодом статуса и Retry-After (429-блокировка логина)
export interface HttpError extends Error {
  status: number
  retryAfter?: number
}

async function jsonFetch<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`) as HttpError
    error.status = response.status
    const retryAfter = Number(response.headers.get('Retry-After'))
    if (Number.isFinite(retryAfter) && retryAfter > 0) error.retryAfter = retryAfter
    throw error
  }
  return (await response.json()) as T
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    accessToken: localStorage.getItem(ACCESS_KEY) ?? '',
    refreshToken: localStorage.getItem(REFRESH_KEY) ?? '',
    // «Запомнить меня»: false — токены живут только в памяти (до перезагрузки)
    remembered: !!localStorage.getItem(ACCESS_KEY),
    user: null as User | null,
  }),
  getters: {
    isAuthenticated: (state) => !!state.accessToken,
    userName: (state) => state.user?.full_name || state.user?.email || '',
  },
  actions: {
    persist(tokens: TokenPair, remember?: boolean) {
      const keep = remember ?? this.remembered
      this.accessToken = tokens.access_token
      this.refreshToken = tokens.refresh_token
      this.remembered = keep
      if (keep) {
        localStorage.setItem(ACCESS_KEY, tokens.access_token)
        localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
      } else {
        // повторный вход без «Запомнить» не должен оставлять старые токены
        localStorage.removeItem(ACCESS_KEY)
        localStorage.removeItem(REFRESH_KEY)
      }
    },
    async login(email: string, password: string, remember = true): Promise<void> {
      const tokens = await jsonFetch<TokenPair>('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      this.persist(tokens, remember)
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
    async apiLogout(): Promise<void> {
      // Отзыв refresh-токена на сервере (security-p0). Best-effort: сырой fetch
      // без Bearer — ошибки сети/протухший токен не блокируют локальный выход.
      if (!this.refreshToken) return
      try {
        await fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
        })
      } catch {
        // офлайн/сеть недоступна — выходим локально в любом случае
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
