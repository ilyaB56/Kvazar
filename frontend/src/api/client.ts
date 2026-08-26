// Обёртка над fetch: базовый URL /api/v1, JSON, Bearer, 401 → refresh →
// повтор запроса → logout при неудаче. Без axios (меньше зависимостей).
import { useAuthStore } from '../stores/auth'

const BASE = '/api/v1'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function rawRequest(path: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(BASE + path, { ...init, headers })
}

// Один параллельный refresh на все запросы (single-flight)
let refreshInFlight: Promise<boolean> | null = null

export async function api<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const auth = useAuthStore()
  const response = await rawRequest(path, init, auth.accessToken)

  if (response.status === 401 && !retried) {
    if (!refreshInFlight) {
      refreshInFlight = auth.refreshTokens().finally(() => {
        refreshInFlight = null
      })
    }
    if (await refreshInFlight) {
      return api<T>(path, init, true)
    }
    auth.forceLogout()
    throw new ApiError(401, 'unauthorized')
  }

  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data: unknown = await response.json()
      if (data && typeof data === 'object' && 'detail' in data) {
        const detail = (data as { detail: unknown }).detail
        message = typeof detail === 'string' ? detail : JSON.stringify(detail)
      }
    } catch {
      // тело не JSON — оставляем HTTP-код
    }
    throw new ApiError(response.status, message)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export const get = <T>(path: string) => api<T>(path)
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' })
