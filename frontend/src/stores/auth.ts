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
    // права роли на модули: {module: 'rw' | 'ro' | 'none'} (редизайн §6.3);
    // null — ещё не загружены (guard дождётся fetchPermissions)
    permissions: null as Record<string, string> | null,
    // sessions-security §2.2: сеансов после последнего входа; >1 —
    // ErpShell показывает модалку «в аккаунт уже вошли» (один раз за вход)
    activeSessions: 0,
    // multitenancy: реестр организаций из логина (для супер-админа)
    organizations: [] as Array<{ id: string; name: string }>,
  }),
  getters: {
    isAuthenticated: (state) => !!state.accessToken,
    // multitenancy §5.2: контекст из access-JWT (без запросов к серверу)
    tokenOrg: (state): string | null => {
      try {
        const payload = JSON.parse(atob(state.accessToken.split('.')[1]
          .replace(/-/g, '+').replace(/_/g, '/')))
        return payload.org ?? null
      } catch {
        return null
      }
    },
    tokenPl: (state): boolean => {
      try {
        const payload = JSON.parse(atob(state.accessToken.split('.')[1]
          .replace(/-/g, '+').replace(/_/g, '/')))
        return !!payload.pl
      } catch {
        return false
      }
    },
    userName: (state) => state.user?.full_name || state.user?.email || '',
    isAdmin: (state) => state.user?.role === 'admin',
  },
  actions: {
    moduleLevel(module: string): string {
      return this.permissions?.[module] ?? 'none'
    },
    // первый раздел, доступный пользователю по правам (редирект запрещённых URL)
    firstAvailableRoute(): string {
      if (this.isAuthenticated) {
        if (this.moduleLevel('accounting') !== 'none' || this.moduleLevel('crm') !== 'none'
          || this.moduleLevel('integrations') !== 'none' || this.moduleLevel('ai') !== 'none') {
          return '/dashboard'
        }
      }
      if (this.moduleLevel('accounting') !== 'none') return '/accounting'
      if (this.moduleLevel('crm') !== 'none') return '/crm'
      if (this.moduleLevel('integrations') !== 'none') return '/integrations/connections'
      if (this.moduleLevel('ai') !== 'none') return '/assistant'
      if (this.user?.role === 'admin') return '/settings/permissions'
      return '/no-access' // экран «Нет доступа» — не молчаливый выход на /login
    },
    async fetchPermissions(): Promise<void> {
      try {
        const data = await get<{ permissions: Record<string, string> }>('/me/permissions')
        this.permissions = data.permissions
      } catch {
        this.permissions = null
      }
    },
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
      this.activeSessions = tokens.active_sessions ?? 1
      this.organizations = tokens.organizations ?? []
      await this.fetchMe()
      await this.fetchPermissions()
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
        this.activeSessions = tokens.active_sessions ?? this.activeSessions
        return true
      } catch {
        return false
      }
    },
    async logoutOthers(password: string): Promise<number> {
      // sessions-security §2.2 (дополнение 2026-09-12): завершить все
      // прочие активные сеансы с подтверждением паролем; текущий (чей
      // refresh передан) остаётся. Сырой fetch — как apiLogout.
      if (!this.refreshToken) return 0
      const response = await fetch('/api/v1/auth/logout-others', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this.refreshToken, password }),
      })
      if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`) as HttpError
        error.status = response.status
        const retryAfter = Number(response.headers.get('Retry-After'))
        if (Number.isFinite(retryAfter) && retryAfter > 0) error.retryAfter = retryAfter
        throw error
      }
      const data = await response.json() as { terminated: number }
      this.activeSessions = 1
      return data.terminated
    },
    async selectOrg(companyId: string): Promise<void> {
      // multitenancy §7.1: пара с org=выбранная, sid сохраняется
      const tokens = await jsonFetch<TokenPair>('/api/v1/auth/select-org', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this.refreshToken, company_id: companyId }),
      })
      this.persist(tokens)
    },
    async leaveOrg(): Promise<void> {
      const tokens = await jsonFetch<TokenPair>('/api/v1/auth/leave-org', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this.refreshToken }),
      })
      this.persist(tokens)
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
      this.activeSessions = 0
      this.accessToken = ''
      this.refreshToken = ''
      this.user = null
      this.permissions = null
      localStorage.removeItem(ACCESS_KEY)
      localStorage.removeItem(REFRESH_KEY)
    },
    forceLogout() {
      this.logout()
      window.location.assign('/login')
    },
  },
})
