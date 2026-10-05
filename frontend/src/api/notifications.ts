// Клиент серверного центра уведомлений (notifications-spec §8, этап A).
// GET /notifications — свои уведомления (новые сверху); format=paginated
// → {items, total} для PaginatedList. Прочитанность — read_at с сервера.
import { get, post } from './client'

export type NotificationSeverity = 'info' | 'warning' | 'critical'

export interface NotificationItem {
  id: string
  kind: string
  severity: NotificationSeverity
  title: string
  body: string
  link: string | null
  entity_type: string | null
  entity_id: string | null
  audience: string | null
  company_id: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationsPage {
  items: NotificationItem[]
  total: number
}

export interface ListNotificationsOptions {
  limit?: number
  offset?: number
  /** true → format=paginated ({items,total}) */
  paginated?: boolean
  /** true → только непрочитанные */
  unread?: boolean
  /** фильтр по kind (запятая) */
  kind?: string
}

export function listNotifications(options: ListNotificationsOptions = {}): Promise<NotificationItem[]> {
  const params = new URLSearchParams()
  params.set('limit', String(options.limit ?? 50))
  if (options.offset !== undefined) params.set('offset', String(options.offset))
  if (options.unread) params.set('unread', 'true')
  if (options.kind) params.set('kind', options.kind)
  return get<NotificationItem[]>(`/notifications?${params.toString()}`)
}

export function listNotificationsPage(options: ListNotificationsOptions = {}): Promise<NotificationsPage> {
  const params = new URLSearchParams()
  params.set('limit', String(options.limit ?? 50))
  if (options.offset !== undefined) params.set('offset', String(options.offset))
  params.set('format', 'paginated')
  if (options.unread) params.set('unread', 'true')
  if (options.kind) params.set('kind', options.kind)
  return get<NotificationsPage>(`/notifications?${params.toString()}`)
}

export function getUnreadCount(): Promise<number> {
  return get<{ count: number }>('/notifications/unread-count')
    .then((data) => data.count)
}

export function markNotificationsRead(payload: { ids: string[] } | { all: boolean }): Promise<number> {
  return post<{ updated: number }>('/notifications/read', payload)
    .then((data) => data.updated)
}
